"""V11.0 fail-closed provenance rules for court-practice records.

This module does not retrieve judgments and does not decide their legal weight.
It only validates the identity and source text of a case-law record before that
record may cross the structured evidence boundary.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Dict, Iterable, Tuple
from urllib.parse import urlsplit


CASE_LAW_PROVENANCE_VERSION = "V11.0-case-law-provenance-1"
_RECORD_ID = re.compile(r"^CASE_[A-Z0-9][A-Z0-9_-]{2,120}$")
_HASH_FIELDS = (
    "source_kind",
    "id",
    "court_name",
    "case_number",
    "decision_date",
    "decision_type",
    "court_level",
    "canonical_url",
    "text",
)


def compute_case_law_record_sha256(record: Dict) -> str:
    """Hash the exact audited provenance fields using canonical JSON."""
    payload = {
        field: str(record.get(field, ""))
        for field in _HASH_FIELDS
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class CaseLawProvenanceVerifier:
    VERSION = CASE_LAW_PROVENANCE_VERSION

    @staticmethod
    def _valid_https_url(value: str) -> bool:
        try:
            parsed = urlsplit(str(value or "").strip())
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and parsed.netloc
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _valid_iso_date(value: str) -> bool:
        raw = str(value or "").strip()
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            return False
        return parsed.isoformat() == raw

    def verify_record(self, record: Dict) -> Tuple[bool, Dict]:
        if not isinstance(record, dict):
            return False, {}
        if str(record.get("source_kind", "")).strip().casefold() != "case_law":
            return False, {}

        record_id = str(record.get("id", "")).strip().upper()
        if not _RECORD_ID.fullmatch(record_id):
            return False, {}

        required_text = {
            key: str(record.get(key, "")).strip()
            for key in (
                "court_name",
                "case_number",
                "decision_date",
                "decision_type",
                "court_level",
                "canonical_url",
                "text",
                "record_sha256",
            )
        }
        if any(not value for value in required_text.values()):
            return False, {}
        if not self._valid_iso_date(required_text["decision_date"]):
            return False, {}
        if not self._valid_https_url(required_text["canonical_url"]):
            return False, {}
        if len(required_text["text"]) < 24:
            return False, {}

        canonical = {
            "source_kind": "case_law",
            "id": record_id,
            "court_name": required_text["court_name"],
            "case_number": required_text["case_number"],
            "decision_date": required_text["decision_date"],
            "decision_type": required_text["decision_type"],
            "court_level": required_text["court_level"],
            "canonical_url": required_text["canonical_url"],
            "text": required_text["text"],
        }
        expected_hash = compute_case_law_record_sha256(canonical)
        if required_text["record_sha256"].casefold() != expected_hash:
            return False, {}
        canonical["record_sha256"] = expected_hash
        canonical["authority_status"] = "not_asserted"
        canonical["provenance_version"] = self.VERSION
        return True, canonical

    def build_map(self, records: Iterable[Dict]) -> Tuple[bool, Dict[str, Dict]]:
        verified: Dict[str, Dict] = {}
        for record in records or ():
            valid, canonical = self.verify_record(record)
            if not valid:
                return False, {}
            record_id = canonical["id"]
            if record_id in verified:
                return False, {}
            verified[record_id] = canonical
        return True, verified
