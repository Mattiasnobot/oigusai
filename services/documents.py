"""Safe, local document extraction with inspectable page/span evidence."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import Dict, List
from xml.etree import ElementTree

import requests
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
import pypdfium2 as pdfium

from services.document_insights import DocumentInsightService


MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
MAX_DOCUMENT_PAGES = 30
MAX_DOCUMENT_CHARS = 120_000
MAX_OCR_PAGES = 8
MAX_IMAGE_PIXELS = 30_000_000
MAX_SPAN_CHARS = 1_200
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg"}


class DocumentProcessingError(ValueError):
    """A document is unsupported, unsafe or cannot be read reliably."""


class LocalDocumentService:
    def __init__(
        self,
        ollama_host: str,
        vision_model: str = "llama3.2-vision",
        timeout: int = 180,
    ):
        self.ollama_host = str(ollama_host or "http://localhost:11434").rstrip("/")
        self.vision_model = vision_model
        self.timeout = int(timeout)

    def process(self, file_name: str, content: bytes) -> Dict:
        safe_name = Path(str(file_name or "document")).name.strip()
        extension = Path(safe_name).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentProcessingError(
                "Toetatud failid on PDF, DOCX, TXT, PNG ja JPG."
            )
        if not content:
            raise DocumentProcessingError("Dokument on tühi.")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise DocumentProcessingError("Dokument võib olla kuni 12 MB.")
        self._validate_signature(extension, content)

        digest = hashlib.sha256(content).hexdigest()
        document_id = f"DOC-{digest[:16].upper()}"
        warnings: List[str] = []
        if extension == ".pdf":
            pages = self._read_pdf(content, warnings)
        elif extension == ".docx":
            pages = self._read_docx(content)
        elif extension == ".txt":
            pages = [{"page": 1, "text": self._decode_text(content), "method": "text"}]
        else:
            pages = [{"page": 1, "text": self._ocr_image(content), "method": "ocr"}]

        total = 0
        clean_pages = []
        for page in pages:
            text = self._clean_text(page.get("text", ""))
            if not text:
                continue
            remaining = MAX_DOCUMENT_CHARS - total
            if remaining <= 0:
                warnings.append("Dokumendi tekst piirati 120 000 märgini.")
                break
            text = text[:remaining]
            total += len(text)
            clean_pages.append({
                "page": int(page.get("page") or 1),
                "text": text,
                "method": str(page.get("method") or "text"),
            })
        if not clean_pages:
            raise DocumentProcessingError(
                "Dokumendist ei õnnestunud loetavat teksti leida. "
                "Skannitud faili puhul proovi selgemat pilti."
            )

        spans = self._build_spans(document_id, safe_name, clean_pages)
        methods = sorted({page["method"] for page in clean_pages})
        document = {
            "document_id": document_id,
            "file_name": safe_name,
            "sha256": digest,
            "file_type": extension.lstrip("."),
            "byte_size": len(content),
            "page_count": max(page["page"] for page in clean_pages),
            "text_length": total,
            "extraction_method": "+".join(methods),
            "spans": spans,
            "warnings": list(dict.fromkeys(warnings)),
        }
        document["insights"] = DocumentInsightService().extract(document)
        return document

    @staticmethod
    def _validate_signature(extension: str, content: bytes) -> None:
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise DocumentProcessingError("Fail ei ole kehtiv PDF.")
        if extension == ".docx" and not content.startswith(b"PK"):
            raise DocumentProcessingError("Fail ei ole kehtiv DOCX.")
        if extension in {".png"} and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DocumentProcessingError("Fail ei ole kehtiv PNG-pilt.")
        if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
            raise DocumentProcessingError("Fail ei ole kehtiv JPEG-pilt.")

    def _read_pdf(self, content: bytes, warnings: List[str]) -> List[Dict]:
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
        except Exception as exc:
            raise DocumentProcessingError("PDF-faili ei õnnestunud avada.") from exc
        if reader.is_encrypted:
            raise DocumentProcessingError("Parooliga kaitstud PDF ei ole toetatud.")
        if len(reader.pages) > MAX_DOCUMENT_PAGES:
            raise DocumentProcessingError("PDF võib sisaldada kuni 30 lehekülge.")

        pages: List[Dict] = []
        ocr_indexes: List[int] = []
        for index, page in enumerate(reader.pages):
            try:
                text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
            except Exception:
                text = ""
            if len(self._clean_text(text)) < 30:
                ocr_indexes.append(index)
            else:
                pages.append({"page": index + 1, "text": text, "method": "pdf_text"})

        if ocr_indexes:
            if len(ocr_indexes) > MAX_OCR_PAGES:
                warnings.append(
                    f"OCR tehti esimesel {MAX_OCR_PAGES} tekstita leheküljel."
                )
            try:
                pdf = pdfium.PdfDocument(content)
                for index in ocr_indexes[:MAX_OCR_PAGES]:
                    page = pdf[index]
                    bitmap = page.render(scale=1.8)
                    image = bitmap.to_pil().convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=90)
                    text = self._ocr_image(buffer.getvalue())
                    if text:
                        pages.append({"page": index + 1, "text": text, "method": "ocr"})
            except DocumentProcessingError:
                raise
            except Exception as exc:
                if not pages:
                    raise DocumentProcessingError(
                        "Skannitud PDF-i OCR ebaõnnestus."
                    ) from exc
                warnings.append("Mõne skannitud lehekülje OCR ebaõnnestus.")
        return sorted(pages, key=lambda item: item["page"])

    @staticmethod
    def _read_docx(content: bytes) -> List[Dict]:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "word/document.xml" not in names:
                    raise DocumentProcessingError("DOCX põhitekst puudub.")
                ordered = ["word/document.xml"] + sorted(
                    name for name in names
                    if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
                )
                selected_info = [archive.getinfo(name) for name in ordered]
                if any(info.file_size > MAX_DOCX_XML_BYTES for info in selected_info):
                    raise DocumentProcessingError("DOCX-i tekstiosa on liiga suur.")
                if sum(info.file_size for info in selected_info) > MAX_DOCX_XML_BYTES:
                    raise DocumentProcessingError("DOCX-i tekstiosad on liiga suured.")
                paragraphs: List[str] = []
                for name in ordered:
                    raw = archive.read(name)
                    root = ElementTree.fromstring(raw)
                    for paragraph in root.iter(
                        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
                    ):
                        text = "".join(
                            node.text or ""
                            for node in paragraph.iter(
                                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                            )
                        ).strip()
                        if text:
                            paragraphs.append(text)
        except DocumentProcessingError:
            raise
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
            raise DocumentProcessingError("DOCX-faili ei õnnestunud lugeda.") from exc
        return [{"page": 1, "text": "\n".join(paragraphs), "method": "docx"}]

    @staticmethod
    def _decode_text(content: bytes) -> str:
        if b"\x00" in content[:4096]:
            raise DocumentProcessingError("TXT-fail näib olevat binaarfail.")
        for encoding in ("utf-8-sig", "utf-16", "cp1257"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DocumentProcessingError("TXT-faili märgikodeeringut ei tuvastatud.")

    def _ocr_image(self, content: bytes) -> str:
        try:
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise DocumentProcessingError("Pildi mõõtmed on liiga suured.")
                image = image.convert("RGB")
                image.thumbnail((2600, 2600))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=92)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        except DocumentProcessingError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise DocumentProcessingError("Pildifaili ei õnnestunud turvaliselt avada.") from exc

        try:
            response = requests.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": self.vision_model,
                    "prompt": (
                        "Transkribeeri selle dokumendipildi kogu nähtav tekst võimalikult "
                        "täpselt. Säilita kuupäevad, summad, nimed, paragrahvid ja "
                        "reavahetused. Ära selgita ega tee järeldusi. Tagasta ainult tekst."
                    ),
                    "images": [encoded],
                    "stream": False,
                    "think": False,
                    "keep_alive": "10m",
                    "options": {"temperature": 0, "num_predict": 4096},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = str(response.json().get("response", "")).strip()
        except Exception as exc:
            raise DocumentProcessingError(
                "Lokaalne OCR-mudel ei ole praegu saadaval."
            ) from exc
        if not text:
            raise DocumentProcessingError("OCR ei leidnud pildilt teksti.")
        return text

    @staticmethod
    def _clean_text(value: str) -> str:
        text = str(value or "").replace("\x00", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def _build_spans(cls, document_id: str, file_name: str, pages: List[Dict]) -> List[Dict]:
        spans: List[Dict] = []
        for page in pages:
            text = page["text"]
            start = 0
            sequence = 1
            while start < len(text):
                end = min(len(text), start + MAX_SPAN_CHARS)
                if end < len(text):
                    boundary = max(
                        text.rfind("\n", start + 400, end),
                        text.rfind(". ", start + 400, end),
                    )
                    if boundary > start:
                        end = boundary + 1
                excerpt = text[start:end].strip()
                if excerpt:
                    actual_start = text.find(excerpt, start, end + 1)
                    actual_end = actual_start + len(excerpt)
                    spans.append({
                        "span_id": f"{document_id}-P{page['page']}-S{sequence}",
                        "document_id": document_id,
                        "file_name": file_name,
                        "page": page["page"],
                        "start": actual_start,
                        "end": actual_end,
                        "text": excerpt,
                        "method": page["method"],
                    })
                    sequence += 1
                start = max(end, start + 1)
        return spans

    @staticmethod
    def focused_excerpt(span: Dict, query: str, max_chars: int = 480) -> Dict:
        """Return a short exact excerpt while retaining source-span coordinates."""
        text = str(span.get("text", ""))
        if len(text) <= max_chars:
            absolute_start = int(span.get("start") or 0)
            return {
                "text": text,
                "start": absolute_start,
                "end": absolute_start + len(text),
            }
        terms = set(re.findall(r"[a-zõäöü]{4,}", str(query or "").casefold()))
        candidates = [
            match
            for match in re.finditer(r"[^\n.!?]+(?:[.!?]|$)", text)
            if match.group(0).strip()
        ]
        best = max(
            candidates,
            key=lambda match: (
                len(terms.intersection(
                    re.findall(r"[a-zõäöü]{4,}", match.group(0).casefold())
                )),
                -match.start(),
            ),
            default=None,
        )
        if best is None:
            local_start = 0
            excerpt = text[:max_chars]
        else:
            local_start = best.start()
            excerpt = best.group(0).strip()
            leading = len(best.group(0)) - len(best.group(0).lstrip())
            local_start += leading
            excerpt = excerpt[:max_chars]
        excerpt = excerpt.rstrip()
        absolute_start = int(span.get("start") or 0) + local_start
        return {
            "text": excerpt,
            "start": absolute_start,
            "end": absolute_start + len(excerpt),
        }
