"""V11.3 fail-closed Riigi Teataja authority/currentness verification.

This layer starts from a V11.2.1 exact official RT XML verification and may
promote only an exact *revision* to a binding source of the right registry
class. It still does not write a corpus or expose live text to retrieval/model
context, and it does not resolve an arbitrary reference to a newer revision.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Protocol, Tuple

from services.legal_source_registry import LegalSourceRegistry
from services import rt_live_source as _live

RT_AUTHORITY_VERSION = "V11.3-rt-authority-currentness-1"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BINDING_CLAIM_CLASS = "binding_rule"
_OPEN_END_MARKERS = frozenset({"", "hetkel kehtiv", "currently valid", "open"})
_SERIES_RE = re.compile(r"^RT\s+(I|II|III|IV)\b", re.IGNORECASE)

_METADATA_ALIASES: Mapping[str, frozenset[str]] = {
    "issuer": frozenset({
        "valjaandja", "valjaandjanimi", "issuer", "issuername",
    }),
    "act_type": frozenset({
        "aktliik", "aktiliik", "dokumendiliik", "documenttype", "acttype",
    }),
    "text_type": frozenset({
        "tekstiliik", "texttype",
    }),
    "valid_from": frozenset({
        "kehtivusealgus", "redaktsioonijoustumine", "redaktsioonijoustumisekp",
        "redaktsioonijoustumiskuupaev", "sonastusejoustumisekp",
        "sonastusejoustumiskuupaev", "validfrom", "revisionvalidfrom",
    }),
    "valid_to": frozenset({
        "kehtivuselopp", "redaktsioonikehtivuselopp", "sonastusekehtivuselopp",
        "validto", "revisionvalidto",
    }),
    "publication_marker": frozenset({
        "avaldamismarge", "avaldamismarke", "publicationmarker",
        "publicationreference", "publicationref",
    }),
}
_ALIAS_TO_FIELD = {
    alias: field
    for field, aliases in _METADATA_ALIASES.items()
    for alias in aliases
}


class RTAuthorityError(RuntimeError):
    """The RT revision cannot support a binding legal claim."""


class _RegistryLike(Protocol):
    def supports_claim(self, source_id: str, claim_class: str) -> bool: ...
    def source(self, source_id: str) -> Dict[str, Any]: ...
    def validates_url(self, source_id: str, url: str) -> bool: ...


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _key(value: Any) -> str:
    raw = str(value or "").rsplit("}", 1)[-1].strip().casefold()
    raw = raw.translate(str.maketrans({
        "õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z",
    }))
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw)


def extract_revision_metadata(xml_bytes: bytes) -> Dict[str, str]:
    """Extract only explicit RT revision metadata fields from verified XML.

    Missing metadata is preserved as missing; this parser never infers dates or
    authority from body text.
    """
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise RTAuthorityError("Riigi Teataja revision metadata requires XML bytes.")
    try:
        root = ET.fromstring(bytes(xml_bytes))
    except ET.ParseError as exc:
        raise RTAuthorityError("Riigi Teataja revision metadata XML is malformed.") from exc

    found: Dict[str, str] = {}
    for element in root.iter():
        field = _ALIAS_TO_FIELD.get(_key(element.tag))
        if field and field not in found:
            found[field] = _clean_text(" ".join(element.itertext()))
        for attr_name, attr_value in element.attrib.items():
            attr_field = _ALIAS_TO_FIELD.get(_key(attr_name))
            if attr_field and attr_field not in found:
                found[attr_field] = _clean_text(attr_value)
    return found


def _parse_date(value: str, field: str) -> date:
    text = _clean_text(value)
    for pattern, order in (
        (r"^(\d{4})-(\d{2})-(\d{2})$", "ymd"),
        (r"^(\d{2})\.(\d{2})\.(\d{4})$", "dmy"),
    ):
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        try:
            if order == "ymd":
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError as exc:
            raise RTAuthorityError(f"Invalid RT {field} date: {text!r}.") from exc
    raise RTAuthorityError(f"RT {field} date is not an audited exact date: {text!r}.")


def _canonical_act_type(value: str) -> str:
    text = _clean_text(value).casefold()
    hits = {kind for kind in ("seadus", "määrus") if re.search(rf"(?<!\w){kind}(?!\w)", text)}
    if len(hits) != 1:
        raise RTAuthorityError(f"RT act type cannot support a binding-rule classification: {value!r}.")
    return hits.pop()


def _publication_series(value: str) -> str:
    match = _SERIES_RE.match(_clean_text(value))
    if not match:
        raise RTAuthorityError("RT publication marker does not expose an audited RT series.")
    return f"RT {match.group(1).upper()}"


def classify_rt_binding_source(metadata: Mapping[str, str]) -> Tuple[str, str, str]:
    """Return (registry source id, authority class, canonical act type)."""
    required = ("issuer", "act_type", "valid_from", "valid_to", "publication_marker")
    missing = [field for field in required if field not in metadata]
    if missing:
        raise RTAuthorityError("RT revision is missing explicit metadata: " + ", ".join(missing))

    act_type = _canonical_act_type(metadata["act_type"])
    series = _publication_series(metadata["publication_marker"])
    if series == "RT I" and act_type in {"seadus", "määrus"}:
        return "RT_NATIONAL_LAW", "binding_national_law", act_type
    if series == "RT IV" and act_type == "määrus":
        return "RT_LOCAL_LAW", "binding_local_law", act_type
    raise RTAuthorityError(
        f"RT series/type combination is not an audited binding source: {series} / {act_type}."
    )


def verify_revision_currentness(metadata: Mapping[str, str], *, as_of: date) -> Tuple[date, date | None]:
    if as_of > date.today():
        raise RTAuthorityError("Future-date RT currentness assertions are disabled.")
    if "valid_from" not in metadata or "valid_to" not in metadata:
        raise RTAuthorityError("RT revision validity interval is incomplete.")

    valid_from = _parse_date(metadata["valid_from"], "valid_from")
    end_raw = _clean_text(metadata["valid_to"])
    valid_to = None if end_raw.casefold() in _OPEN_END_MARKERS else _parse_date(end_raw, "valid_to")
    if as_of < valid_from:
        raise RTAuthorityError("RT revision was not yet in force on the requested date.")
    if valid_to is not None and as_of >= valid_to:
        raise RTAuthorityError("RT revision was no longer in force on the requested date.")
    return valid_from, valid_to


def compute_revision_provenance_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_live_rt_binding_authority(
    reference: str,
    *,
    as_of: date | None = None,
    timeout: float = 20.0,
    user_agent: str = "OigusAI/11.3 rt-authority-verifier",
    fetcher: Callable[[str, float, str], Tuple[bytes, str]] | None = None,
    registry: _RegistryLike | None = None,
) -> Dict[str, Any]:
    """Verify an exact RT revision as a current binding source for one date."""
    captured: Dict[str, bytes] = {}
    base_fetch = fetcher or _live._network_fetch

    def capturing_fetch(url: str, fetch_timeout: float, fetch_user_agent: str) -> Tuple[bytes, str]:
        data, final_url = base_fetch(url, fetch_timeout, fetch_user_agent)
        captured["xml"] = bytes(data)
        return data, final_url

    try:
        exact = _live.verify_live_rt_source(
            reference,
            timeout=timeout,
            user_agent=user_agent,
            fetcher=capturing_fetch,
        )
    except _live.RTLiveSourceError as exc:
        raise RTAuthorityError(str(exc)) from exc

    xml_bytes = captured.get("xml")
    if not xml_bytes:
        raise RTAuthorityError("Verified RT fetch did not retain revision metadata bytes for classification.")
    metadata = extract_revision_metadata(xml_bytes)
    source_id, expected_authority, act_type = classify_rt_binding_source(metadata)
    check_date = as_of or date.today()
    valid_from, valid_to = verify_revision_currentness(metadata, as_of=check_date)

    source_registry = registry or LegalSourceRegistry.load(PROJECT_ROOT)
    if not source_registry.supports_claim(source_id, _BINDING_CLAIM_CLASS):
        raise RTAuthorityError(f"Registry source {source_id} is not allowed to support binding_rule claims.")
    source_entry = source_registry.source(source_id)
    if source_entry.get("authority_class") != expected_authority:
        raise RTAuthorityError(f"Registry authority class drifted for {source_id}.")
    if not source_registry.validates_url(source_id, exact["canonical_url"]):
        raise RTAuthorityError(f"Registry rejected the canonical RT URL for {source_id}.")

    provenance_payload = {
        "version": RT_AUTHORITY_VERSION,
        "act_id": exact["act_id"],
        "title": exact["title"],
        "source_id": source_id,
        "authority_class": expected_authority,
        "issuer": metadata["issuer"],
        "act_type": act_type,
        "text_type": metadata.get("text_type", ""),
        "valid_from": valid_from.isoformat(),
        "valid_to_exclusive": valid_to.isoformat() if valid_to else None,
        "publication_marker": metadata["publication_marker"],
        "xml_sha256": exact["xml_sha256"],
        "text_sha256": exact["text_sha256"],
    }
    return {
        **exact,
        "version": RT_AUTHORITY_VERSION,
        "status": "BINDING_SOURCE_VERIFIED",
        "source_id": source_id,
        "claim_class": _BINDING_CLAIM_CLASS,
        "authority_class": expected_authority,
        "authority_verified": True,
        "currentness_verified": True,
        "as_of_date": check_date.isoformat(),
        "issuer": metadata["issuer"],
        "act_type": act_type,
        "text_type": metadata.get("text_type", ""),
        "publication_marker": metadata["publication_marker"],
        "valid_from": valid_from.isoformat(),
        "valid_to_exclusive": valid_to.isoformat() if valid_to else None,
        "revision_provenance_sha256": compute_revision_provenance_sha256(provenance_payload),
        "retrieval_enabled": False,
        "model_context_enabled": False,
        "corpus_write_enabled": False,
        "current_revision_resolution_enabled": False,
    }
