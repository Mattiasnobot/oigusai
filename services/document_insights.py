"""V8.3 deterministic document facts, timeline and safe draft templates."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List


DATE_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])[.](?:0?[1-9]|1[0-2])[.](?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"
)
AMOUNT_RE = re.compile(
    r"(?<!\w)(?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.]\d{1,2})?\s*(?:€|eurot?|eur)\b",
    re.IGNORECASE,
)
REFERENCE_RE = re.compile(r"(?:§\s*\d+[\w¹²³⁴⁵⁶⁷⁸⁹-]*|\b[A-ZÕÄÖÜ]{2,12}[_ ]\d+[A-Z0-9]*)")
IMPORTANT_TERMS = (
    "tähtaeg", "vaidlusta", "kaeb", "tasuma", "kohustatud", "nõuab",
    "ülesütle", "lõpet", "trahv", "otsus", "hoiatus", "allkir",
)


def _sentences(text: str):
    for match in re.finditer(r"[^\n.!?]+(?:[.!?]|$)", str(text or "")):
        clean = match.group(0).strip()
        if clean:
            leading = len(match.group(0)) - len(match.group(0).lstrip())
            yield clean, match.start() + leading, match.start() + leading + len(clean)


class DocumentInsightService:
    """Extract only exact, source-addressable observations from document spans."""

    def extract(self, document: Dict) -> Dict:
        dates: List[Dict] = []
        amounts: List[Dict] = []
        references: List[Dict] = []
        timeline: List[Dict] = []
        important: List[Dict] = []
        seen = {"dates": set(), "amounts": set(), "references": set(), "important": set()}

        for span in document.get("spans", [])[:200]:
            text = str(span.get("text") or "")
            for match in DATE_RE.finditer(text):
                sentence, local_start, local_end = self._context(text, match.start())
                source = self._source(span, sentence, local_start, local_end)
                self._append_unique(dates, seen["dates"], match.group(0), source)
                key = (match.group(0).casefold(), sentence.casefold())
                if key not in {
                    (item["date"].casefold(), item["text"].casefold())
                    for item in timeline
                }:
                    timeline.append({"date": match.group(0), "text": sentence, "source": source})
            for match in AMOUNT_RE.finditer(text):
                sentence, local_start, local_end = self._context(text, match.start())
                self._append_unique(
                    amounts,
                    seen["amounts"],
                    match.group(0),
                    self._source(span, sentence, local_start, local_end),
                )
            for match in REFERENCE_RE.finditer(text):
                sentence, local_start, local_end = self._context(text, match.start())
                self._append_unique(
                    references,
                    seen["references"],
                    match.group(0),
                    self._source(span, sentence, local_start, local_end),
                )
            for sentence, local_start, local_end in _sentences(text):
                source = self._source(span, sentence, local_start, local_end)
                if any(term in sentence.casefold() for term in IMPORTANT_TERMS):
                    key = sentence.casefold()
                    if key not in seen["important"]:
                        seen["important"].add(key)
                        important.append({"text": sentence, "source": source})

        return {
            "dates": dates[:30],
            "amounts": amounts[:30],
            "references": references[:30],
            "timeline": timeline[:40],
            "important_excerpts": important[:30],
            "review_checklist": self._checklist(document, dates, amounts, important),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "method": "deterministic_exact_spans",
        }

    @staticmethod
    def _context(text: str, offset: int) -> tuple[str, int, int]:
        """Return a readable exact excerpt around a match, preserving dates with dots."""
        start = max(text.rfind("\n", 0, offset), text.rfind(". ", 0, offset))
        start = 0 if start < 0 else start + (2 if text[start:start + 2] == ". " else 1)
        end_candidates = [value for value in (
            text.find("\n", offset), text.find(". ", offset)
        ) if value >= 0]
        end = min(end_candidates) + 1 if end_candidates else len(text)
        raw = text[start:end]
        leading = len(raw) - len(raw.lstrip())
        clean = raw.strip()
        return clean, start + leading, start + leading + len(clean)

    @staticmethod
    def _source(span: Dict, evidence: str, local_start: int, local_end: int) -> Dict:
        absolute = int(span.get("start") or 0)
        return {
            "span_id": str(span.get("span_id") or ""),
            "document_id": str(span.get("document_id") or ""),
            "file_name": str(span.get("file_name") or ""),
            "page": int(span.get("page") or 1),
            "start": absolute + local_start,
            "end": absolute + local_end,
            "evidence": evidence,
            "method": str(span.get("method") or "text"),
        }

    @staticmethod
    def _append_unique(target: List[Dict], seen: set, value: str, source: Dict) -> None:
        key = (value.casefold(), source["span_id"], source["start"])
        if key in seen:
            return
        seen.add(key)
        target.append({"value": value, "source": source})

    @staticmethod
    def _checklist(document: Dict, dates: List[Dict], amounts: List[Dict], important: List[Dict]) -> List[str]:
        checks = ["Kontrolli dokumendi pealkirja, koostajat ja kättesaamise kuupäeva."]
        if dates:
            checks.append("Kontrolli kõik väljaloetud kuupäevad dokumendi originaalilt.")
        if amounts:
            checks.append("Kontrolli summad, valuuta ning see, kellelt ja mille eest tasu nõutakse.")
        if important:
            checks.append("Vaata üle tähistatud kohustused, tähtajad ja vaidlustamisjuhised.")
        if "ocr" in str(document.get("extraction_method") or ""):
            checks.append("OCR-tekst võib sisaldada vigu; kontrolli nimed, kuupäevad ja summad pildilt.")
        checks.append("Säilita dokumendi originaalfail ja selle kättesaamist tõendav info.")
        return checks


class SafeDraftService:
    """Create editable templates from confirmed card fields without legal invention."""

    ALLOWED_TYPES = {
        "selgitustaotlus": "Selgitustaotlus",
        "vaie": "Vaide kavand",
        "noudekiri": "Nõudekirja kavand",
        "vastus": "Vastuse kavand",
    }

    @staticmethod
    def _first_person_goal(value: str) -> str:
        """Turn the card's UI-oriented goal into a natural first-person request."""
        goal = str(value or "").strip()
        replacements = (
            ("Kasutaja soovib ", "Soovin "),
            ("Kasutaja tahab ", "Soovin "),
            ("Soovid ", "Soovin "),
            ("Soovib ", "Soovin "),
            ("Tahab ", "Soovin "),
        )
        for prefix, replacement in replacements:
            if goal.casefold().startswith(prefix.casefold()):
                return replacement + goal[len(prefix):]
        return goal

    def build(self, draft_type: str, case_card: Dict, documents: Iterable[Dict]) -> Dict:
        normalized = str(draft_type or "").strip().casefold()
        if normalized not in self.ALLOWED_TYPES:
            raise ValueError("Dokumendikavandi liik ei ole toetatud.")
        summary = str(case_card.get("summary") or "").strip() or "[KIRJELDA LÜHIDALT JUHTUNUT]"
        goal = self._first_person_goal(case_card.get("user_goal")) or "[KIRJELDA SOOVITUD LAHENDUST]"
        events = [item for item in case_card.get("events", []) if isinstance(item, dict)]
        event_lines = [
            "- " + " – ".join(filter(None, (
                str(item.get("date") or "").strip(),
                str(item.get("action") or item.get("evidence") or "").strip(),
            )))
            for item in events[:12]
        ]
        document_lines = [
            f"- {str(item.get('file_name') or 'Dokument')}"
            for item in list(documents)[:20]
        ]
        body = "\n".join([
            self.ALLOWED_TYPES[normalized],
            "",
            "Saaja: [TÄIDA SAAJA NIMI JA AADRESS]",
            "Esitaja: [TÄIDA OMA NIMI JA KONTAKT]",
            "Kuupäev: [TÄIDA]",
            "",
            "Asjaolud",
            summary,
            *( ["", "Sündmuste ajajoon", *event_lines] if event_lines else [] ),
            "",
            "Minu taotlus",
            goal,
            "",
            "Lisad",
            *(document_lines or ["- [LISA VAJALIKUD DOKUMENDID]"]),
            "",
            "Lugupidamisega",
            "[NIMI]",
        ])
        return {
            "draft_type": normalized,
            "title": self.ALLOWED_TYPES[normalized],
            "body": body,
            "placeholders_present": "[" in body,
            "warning": (
                "See on kasutaja kinnitatud faktidest koostatud kavand. Kontrolli saaja, "
                "tähtajad, õiguslik alus ja kõik nurksulgudes väljad enne saatmist."
            ),
        }
