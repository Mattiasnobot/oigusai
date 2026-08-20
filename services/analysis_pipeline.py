"""V9.0 auditable analysis stages and verified answer packaging."""

from __future__ import annotations

import re
import time
import uuid
from typing import Dict, Iterable, List, Optional


class AnalysisPipelineRun:
    """Retain stage metadata only; never retain the user's text."""

    ORDER = (
        "case_understanding",
        "document_evidence",
        "legal_retrieval",
        "model_analysis",
        "source_verification",
        "evidence_verification",
        "answer_packaging",
    )

    def __init__(self):
        self.run_id = str(uuid.uuid4())
        self.started = time.perf_counter()
        self._stages: Dict[str, Dict] = {}

    def complete(self, name: str, **metadata) -> None:
        if name not in self.ORDER:
            raise ValueError(f"Tundmatu analüüsietapp: {name}")
        clean_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, (bool, int, float)) or value is None:
                clean_metadata[str(key)[:60]] = value
            elif isinstance(value, str):
                clean_metadata[str(key)[:60]] = value[:120]
        self._stages[name] = {
            "name": name,
            "status": "completed",
            "metadata": clean_metadata,
        }

    def fail(self, name: str, code: str = "failed") -> None:
        if name in self.ORDER:
            self._stages[name] = {
                "name": name,
                "status": "failed",
                "metadata": {"code": str(code)[:80]},
            }

    def public(self) -> Dict:
        stages = []
        for name in self.ORDER:
            stages.append(self._stages.get(name, {
                "name": name,
                "status": "not_run",
                "metadata": {},
            }))
        return {
            "run_id": self.run_id,
            "status": "completed" if all(
                item["status"] == "completed" for item in stages
            ) else "partial",
            "duration_ms": round((time.perf_counter() - self.started) * 1000, 1),
            "stages": stages,
            "retains_user_text": False,
        }


class VerifiedAnswerBuilder:
    """Turn verified claims into a layered, user-friendly response object."""

    LEGAL_STATUSES = {
        "EVIDENCE_VERIFIED",
        "INPUTS_VERIFIED",
        "DOCUMENT_TEXT_VERIFIED",
        "OCR_REVIEW_REQUIRED",
    }

    def build(
        self,
        *,
        analysis: str,
        claims: Iterable[Dict],
        verification_status: str,
        warning: str,
        case_card: Optional[Dict] = None,
        urgency: Optional[Dict] = None,
        fallback_used: bool = False,
    ) -> Dict:
        claims = list(claims or [])
        legal_claims = [
            claim for claim in claims
            if claim.get("kind") != "document_excerpt"
            and claim.get("verification_status") in self.LEGAL_STATUSES
            and claim.get("sources")
        ]
        document_claims = [
            claim for claim in claims if claim.get("kind") == "document_excerpt"
        ]

        short_parts = self._section_sentences(analysis, "LÜHIVASTUS", 3)
        if not short_parts:
            short_parts = self._section_sentences(analysis, "ÕIGUSLIK KOHALDAMINE", 3)
        if not short_parts:
            short_parts = [str(claim.get("text") or "").strip() for claim in legal_claims[:2]]
        if not short_parts:
            short_parts = self._first_sentences(analysis, 3)

        why = []
        for claim in legal_claims[:8]:
            why.append({
                "text": str(claim.get("text") or "").strip(),
                "verification_status": str(claim.get("verification_status") or ""),
                "source_ids": [
                    str(source.get("id") or "")
                    for source in claim.get("sources", [])
                    if str(source.get("id") or "").strip()
                ],
            })

        next_steps = self._section_sentences(analysis, "SOOVITUSED", 6)
        if not next_steps:
            next_steps = [
                "Säilita asjaga seotud dokumendid ja pane kirja nende kättesaamise kuupäevad.",
                "Kontrolli dokumendi täpset pealkirja, koostajat ja vaidlustamisjuhist.",
            ]

        case_card = case_card or {}
        urgency = urgency or {}
        unknowns = []
        for value in list(case_card.get("missing_facts") or []) + list(urgency.get("questions") or []):
            clean = " ".join(str(value or "").split())
            if clean and clean.casefold() not in {item.casefold() for item in unknowns}:
                unknowns.append(clean)
            if len(unknowns) >= 10:
                break

        if fallback_used or verification_status == "SOURCE_ONLY_FALLBACK":
            confidence = "limited"
            confidence_reason = "Kohalik mudelivastus asendati kontrollitud allikate kokkuvõttega."
        elif verification_status in {"OCR_REVIEW_REQUIRED", "CITATIONS_VERIFIED"} or unknowns:
            confidence = "conditional"
            confidence_reason = "Vastus sisaldab kontrollimist vajavat sisendit või puuduvaid asjaolusid."
        else:
            confidence = "supported"
            confidence_reason = "Vastuse tõendiväited ja kasutatud allikad läbisid kontrolli."

        return {
            "short_answer": re.sub(
                r"\s*\[[A-ZÕÄÖÜ0-9_]+\]", "", " ".join(short_parts)
            ).strip(),
            "why": why,
            "next_steps": next_steps,
            "unknowns": unknowns,
            "document_evidence_count": len(document_claims),
            "urgency": urgency,
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "warning": str(warning or ""),
        }

    @staticmethod
    def _first_sentences(text: str, limit: int) -> List[str]:
        result = []
        for match in re.finditer(r"[^\n.!?]+(?:[.!?]|$)", str(text or "")):
            clean = match.group(0).strip()
            if clean and not clean.endswith(":"):
                result.append(clean)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _section_sentences(cls, text: str, heading: str, limit: int) -> List[str]:
        pattern = re.compile(
            rf"(?:^|\n){re.escape(heading)}\s*:\s*(.*?)(?=\n[A-ZÕÄÖÜ][A-ZÕÄÖÜ\s]+:|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(str(text or ""))
        if not match:
            return []
        lines = []
        for raw in match.group(1).splitlines():
            clean = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", raw).strip()
            if clean:
                lines.append(clean)
            if len(lines) >= limit:
                break
        if lines:
            return lines
        return cls._first_sentences(match.group(1), limit)
