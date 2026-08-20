"""V7 structured-evidence boundary for law and document claims.

This verifier deliberately proves a narrow thing: every displayed evidence
fragment is present in the source supplied to the API boundary.  Cross-source
comparisons are labelled ``INPUTS_VERIFIED`` because exact inputs do not, by
themselves, prove that a legal conclusion is materially correct.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple


class EvidenceVerifier:
    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    @staticmethod
    def _normalize_id(value: str) -> str:
        return re.sub(r"[^A-ZÕÄÖÜ0-9_-]", "", str(value or "").upper())

    def verify(
        self,
        claims: Iterable[Dict],
        laws: Iterable[Dict],
        document_spans: Iterable[Dict] = (),
    ) -> Tuple[bool, List[Dict]]:
        law_map = {
            self._normalize_id(law.get("id")): law
            for law in laws
            if law.get("id")
        }
        span_map = {
            self._normalize_id(span.get("span_id")): span
            for span in document_spans
            if span.get("span_id")
        }
        verified: List[Dict] = []
        for claim in claims or []:
            if not isinstance(claim, dict) or not str(claim.get("text", "")).strip():
                return False, []
            sources = claim.get("sources")
            if not isinstance(sources, list) or not sources:
                return False, []
            claim_kind = str(claim.get("kind", "")).strip().casefold()
            status = str(claim.get("verification_status", "")).strip().upper()
            source_kinds = set()
            clean_sources: List[Dict] = []
            for source in sources:
                if not isinstance(source, dict):
                    return False, []
                kind = str(source.get("kind", "")).strip().casefold()
                source_id = self._normalize_id(source.get("id"))
                evidence = self._normalize(source.get("evidence"))
                if not evidence:
                    return False, []
                if kind == "law":
                    law = law_map.get(source_id)
                    if law is None or evidence not in self._normalize(law.get("text")):
                        return False, []
                    clean_sources.append({
                        "kind": "law",
                        "id": str(law.get("id", "")),
                        "title": str(law.get("title", law.get("id", ""))),
                        "source": str(law.get("source", "")),
                        "evidence": str(source.get("evidence", "")).strip(),
                    })
                elif kind == "document":
                    span = span_map.get(source_id)
                    if span is None or evidence not in self._normalize(span.get("text")):
                        return False, []
                    raw_span = str(span.get("text", ""))
                    raw_evidence = str(source.get("evidence", "")).strip()
                    local_start = raw_span.find(raw_evidence)
                    if local_start < 0:
                        return False, []
                    evidence_start = int(span.get("start") or 0) + local_start
                    evidence_end = evidence_start + len(raw_evidence)
                    if source.get("page") not in (None, span.get("page")):
                        return False, []
                    if source.get("start") not in (None, evidence_start):
                        return False, []
                    if source.get("end") not in (None, evidence_end):
                        return False, []
                    clean_sources.append({
                        "kind": "document",
                        "id": str(span.get("span_id", "")),
                        "document_id": str(span.get("document_id", "")),
                        "title": str(span.get("file_name", "")),
                        "source": f"Dokument, lk {span.get('page', 1)}",
                        "evidence": str(source.get("evidence", "")).strip(),
                        "page": int(span.get("page") or 1),
                        "start": evidence_start,
                        "end": evidence_end,
                        "method": str(span.get("method") or "text"),
                    })
                else:
                    return False, []
                source_kinds.add(kind)
            if claim_kind == "inference":
                if status != "INPUTS_VERIFIED" or not {
                    "law", "document"
                }.issubset(source_kinds):
                    return False, []
            elif claim_kind == "law":
                if status not in {"EVIDENCE_VERIFIED", "CITATION_VERIFIED"} or source_kinds != {"law"}:
                    return False, []
            elif claim_kind == "document_excerpt":
                expected = (
                    "OCR_REVIEW_REQUIRED"
                    if any(source.get("method") == "ocr" for source in clean_sources)
                    else "DOCUMENT_TEXT_VERIFIED"
                )
                if status != expected or source_kinds != {"document"}:
                    return False, []
            else:
                return False, []
            clean_claim = dict(claim)
            clean_claim["sources"] = clean_sources
            verified.append(clean_claim)
        return bool(verified), verified
