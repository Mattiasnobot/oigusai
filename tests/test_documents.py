import io
import unittest
import zipfile
from unittest.mock import Mock, patch

from PIL import Image

from services.documents import DocumentProcessingError, LocalDocumentService
from services.matters import MatterNotFoundError, MatterStore


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = LocalDocumentService("http://localhost:11434")

    def test_txt_gets_exact_span_coordinates(self):
        result = self.service.process(
            "teade.txt",
            "Trahvisumma on 4000 eurot. Dokument saadi 10. augustil.".encode(),
        )

        self.assertEqual(result["file_type"], "txt")
        self.assertEqual(result["page_count"], 1)
        span = result["spans"][0]
        self.assertEqual(span["text"], "Trahvisumma on 4000 eurot. Dokument saadi 10. augustil.")
        self.assertEqual(span["start"], 0)
        self.assertEqual(span["end"], len(span["text"]))

    def test_spoofed_pdf_is_rejected(self):
        with self.assertRaises(DocumentProcessingError):
            self.service.process("otsus.pdf", b"not a pdf")

    def test_docx_text_is_extracted_without_macros_or_paths(self):
        stream = io.BytesIO()
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            'Menetleja otsus ja kaebetähtaeg.'
            '</w:t></w:r></w:p></w:body></w:document>'
        )
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", xml)

        result = self.service.process("otsus.docx", stream.getvalue())

        self.assertEqual(result["extraction_method"], "docx")
        self.assertIn("kaebetähtaeg", result["spans"][0]["text"])

    def test_image_ocr_uses_local_vision_model_and_keeps_text(self):
        image_stream = io.BytesIO()
        Image.new("RGB", (120, 60), "white").save(image_stream, format="PNG")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"response": "OTSUS\nSumma 4000 eurot"}
        with patch("services.documents.requests.post", return_value=response) as post:
            result = self.service.process("scan.png", image_stream.getvalue())

        self.assertEqual(result["extraction_method"], "ocr")
        self.assertIn("4000 eurot", result["spans"][0]["text"])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "llama3.2-vision")


class MatterStoreTests(unittest.TestCase):
    def test_matter_is_memory_only_and_returns_relevant_spans(self):
        store = MatterStore()
        matter = store.create("Trahviasi")
        store.add_document(matter["matter_id"], {
            "document_id": "DOC-1",
            "file_name": "otsus.txt",
            "sha256": "abc",
            "file_type": "txt",
            "byte_size": 10,
            "page_count": 1,
            "text_length": 30,
            "extraction_method": "text",
            "warnings": [],
            "spans": [
                {"span_id": "DOC-1-P1-S1", "document_id": "DOC-1", "file_name": "otsus.txt", "page": 1, "start": 0, "end": 12, "text": "Muu sissejuhatus", "method": "text"},
                {"span_id": "DOC-1-P1-S2", "document_id": "DOC-1", "file_name": "otsus.txt", "page": 1, "start": 13, "end": 30, "text": "Kaebetähtaeg on möödas", "method": "text"},
            ],
        })

        spans = store.relevant_spans(
            matter["matter_id"], ["DOC-1"], "kaebetähtaeg", limit=1
        )

        self.assertEqual(spans[0]["span_id"], "DOC-1-P1-S2")
        self.assertTrue(store.delete(matter["matter_id"]))
        with self.assertRaises(MatterNotFoundError):
            store.get(matter["matter_id"])

    def test_inactive_matter_expires_from_memory(self):
        now = [1000.0]
        store = MatterStore(ttl_minutes=5, clock=lambda: now[0])
        matter = store.create("Ajutine juhtum")

        now[0] += 301

        self.assertEqual(store.count(), 0)
        with self.assertRaises(MatterNotFoundError):
            store.get(matter["matter_id"])


if __name__ == "__main__":
    unittest.main()
