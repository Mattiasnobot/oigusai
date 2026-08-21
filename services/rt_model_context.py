"""V11.5 admission gate for verified Riigi Teataja evidence entering model context.

The gate does not retrieve law and does not create legal authority. It accepts
only V11.4 records that already carry BINDING_SECTION_VERIFIED provenance, or
explicit audited-local fallback records. Any live-shaped record that fails an
integrity check is rejected before it can be sent to an LLM.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Dict, Mapping, Sequence
from urllib.parse import urlsplit

from services.rt_current_retrieval import RT_CURRENT_RETRIEVAL_VERSION
from services.rt_section_evidence import (
    canonical_section,
    compute_section_provenance_sha256,
)

RT_MODEL_CONTEXT_VERSION = "V11.5-verified-live-model-context-1"
_LIVE_STATUS = "BINDING_SECTION_VERIFIED"
_LOCAL_STATUS = "LOCAL_CORPUS_FALLBACK"
_LIVE_EVIDENCE_SOURCE = "rt_live_verified"
_LOCAL_EVIDENCE_SOURCE = "audited_local_corpus"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_AUTHORITIES = {
    "RT_NATIONAL_LAW": "binding_national_law",
    "RT_LOCAL_LAW": "binding_local_law",
}


class RTModelContextError(RuntimeError):
    """A law record is not safe to admit to model context."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _require_hex64(value: Any, field: str) -> str:
    text = _clean(value).casefold()
    if not _HEX64.fullmatch(text):
        raise RTModelContextError(f"{field} is not a canonical SHA-256 digest.")
    return text


def _parse_as_of(value: Any) -> date:
    text = _clean(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise RTModelContextError("as_of_date must be an exact YYYY-MM-DD date.") from exc
    if parsed > date.today():
        raise RTModelContextError("Future-date model-context admission is disabled.")
    return parsed


def _validate_exact_rt_urls(record: Mapping[str, Any], act_id: str, section: str) -> None:
    canonical = _clean(record.get("canonical_url"))
    expected_canonical = f"https://www.riigiteataja.ee/akt/{act_id}"
    if canonical != expected_canonical:
        raise RTModelContextError("canonical_url is not the exact audited Riigi Teataja act URL.")

    xml_url = _clean(record.get("xml_url"))
    expected_xml = f"https://www.riigiteataja.ee/public-api/api/v1/akt/{act_id}/xml"
    if xml_url != expected_xml:
        raise RTModelContextError("xml_url is not the exact audited Riigi Teataja XML URL.")

    source_url = _clean(record.get("url"))
    parsed = urlsplit(source_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "www.riigiteataja.ee"
        or parsed.path != f"/akt/{act_id}"
        or parsed.query
        or parsed.fragment.casefold() != f"para{section.casefold()}"
    ):
        raise RTModelContextError("section URL does not point to the exact verified RT section anchor.")


def validate_verified_live_record(
    record: Mapping[str, Any],
    *,
    expected_as_of: date | None = None,
) -> Dict[str, Any]:
    """Validate one V11.4 live section and return a model-admitted copy."""
    if _clean(record.get("verification_status")) != _LIVE_STATUS:
        raise RTModelContextError("Live model context requires BINDING_SECTION_VERIFIED.")
    if _clean(record.get("evidence_source")) != _LIVE_EVIDENCE_SOURCE:
        raise RTModelContextError("Live model context requires rt_live_verified evidence provenance.")
    if record.get("authority_verified") is not True:
        raise RTModelContextError("Live model context requires authority_verified=true.")
    if record.get("currentness_verified") is not True:
        raise RTModelContextError("Live model context requires currentness_verified=true.")
    if record.get("corpus_write_enabled") is not False:
        raise RTModelContextError("Verified live context must remain non-persistent.")

    source_id = _clean(record.get("source_id"))
    authority_class = _clean(record.get("authority_class"))
    expected_authority = _ALLOWED_AUTHORITIES.get(source_id)
    if expected_authority is None or authority_class != expected_authority:
        raise RTModelContextError("Live source/authority class is not an audited binding mapping.")

    act_id = _clean(record.get("act_id"))
    if not act_id.isdigit():
        raise RTModelContextError("Verified live record is missing a numeric act_id.")

    section = canonical_section(_clean(record.get("section")))
    _validate_exact_rt_urls(record, act_id, section)

    as_of = _parse_as_of(record.get("as_of_date"))
    if expected_as_of is not None and as_of != expected_as_of:
        raise RTModelContextError("Verified live record was checked for a different legal date.")

    text = str(record.get("text") or "")
    if not text.strip():
        raise RTModelContextError("Verified live section text is empty.")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if _clean(record.get("content_hash")).casefold() != content_hash:
        raise RTModelContextError("Verified live section content_hash does not match its text.")

    revision_hash = _require_hex64(
        record.get("revision_provenance_sha256"), "revision_provenance_sha256"
    )
    _require_hex64(record.get("xml_sha256"), "xml_sha256")
    section_hash = _require_hex64(
        record.get("section_provenance_sha256"), "section_provenance_sha256"
    )
    expected_section_hash = compute_section_provenance_sha256({
        "version": RT_CURRENT_RETRIEVAL_VERSION,
        "act_id": act_id,
        "revision_provenance_sha256": revision_hash,
        "section": section,
        "content_hash": content_hash,
    })
    if section_hash != expected_section_hash:
        raise RTModelContextError("Verified live section provenance chain does not match the record.")

    admitted = dict(record)
    admitted["model_context_enabled"] = True
    admitted["model_context_version"] = RT_MODEL_CONTEXT_VERSION
    admitted["model_context_admission"] = "VERIFIED_LIVE_BINDING_SECTION"
    return admitted


def validate_local_fallback_record(
    record: Mapping[str, Any],
    *,
    reference: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Admit an explicit V11.4 local fallback only if it matches its audited input."""
    if reference is None:
        raise RTModelContextError("Local fallback admission requires its audited corpus input record.")
    if _clean(record.get("verification_status")) != _LOCAL_STATUS:
        raise RTModelContextError("Local model context requires explicit LOCAL_CORPUS_FALLBACK status.")
    if _clean(record.get("evidence_source")) != _LOCAL_EVIDENCE_SOURCE:
        raise RTModelContextError("Local fallback must retain audited_local_corpus provenance.")
    for field in ("id", "text", "content_hash", "url", "law_name", "section"):
        if str(record.get(field, "")) != str(reference.get(field, "")):
            raise RTModelContextError(
                f"Local fallback field {field} drifted from its audited corpus input."
            )
    text = str(record.get("text") or "")
    if not text.strip():
        raise RTModelContextError("Local fallback section text is empty.")
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if _clean(record.get("content_hash")).casefold() != expected_hash:
        raise RTModelContextError("Local fallback content_hash does not match its audited text.")
    url = _clean(record.get("url"))
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "www.riigiteataja.ee":
        raise RTModelContextError("Local fallback does not retain an audited Riigi Teataja URL.")

    admitted = dict(record)
    admitted["model_context_enabled"] = True
    admitted["model_context_version"] = RT_MODEL_CONTEXT_VERSION
    admitted["model_context_admission"] = "AUDITED_LOCAL_CORPUS_FALLBACK"
    return admitted


def admit_model_context(
    laws: Sequence[Mapping[str, Any]],
    *,
    expected_as_of: date | None = None,
    local_reference: Mapping[str, Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Admit only explicit V11.4 live/fallback records to the model."""
    admitted: list[Dict[str, Any]] = []
    live_count = 0
    local_count = 0

    for record in laws:
        status = _clean(record.get("verification_status"))
        evidence_source = _clean(record.get("evidence_source"))
        if status == _LIVE_STATUS or evidence_source == _LIVE_EVIDENCE_SOURCE:
            admitted.append(
                validate_verified_live_record(record, expected_as_of=expected_as_of)
            )
            live_count += 1
            continue
        if status == _LOCAL_STATUS or evidence_source == _LOCAL_EVIDENCE_SOURCE:
            record_id = _clean(record.get("id")).upper()
            reference = (local_reference or {}).get(record_id)
            admitted.append(
                validate_local_fallback_record(record, reference=reference)
            )
            local_count += 1
            continue
        raise RTModelContextError(
            "Model-context admission accepts only explicit V11.4 verified-live "
            "or audited-local-fallback records."
        )

    status = (
        "VERIFIED_LIVE_CONTEXT"
        if admitted and live_count == len(admitted)
        else "MIXED_VERIFIED_AND_LOCAL_CONTEXT"
        if live_count and local_count
        else "AUDITED_LOCAL_CONTEXT"
        if local_count
        else "EMPTY_CONTEXT"
    )
    return {
        "version": RT_MODEL_CONTEXT_VERSION,
        "status": status,
        "laws": admitted,
        "live_count": live_count,
        "local_count": local_count,
        "model_context_enabled": bool(admitted),
        "unverified_live_admitted": False,
        "corpus_write_enabled": False,
    }
