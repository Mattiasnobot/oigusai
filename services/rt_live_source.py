"""V11.2.1 Riigi Teataja exact live-source verifier.

This adapter proves only that bytes were fetched from the audited Riigi Teataja
XML API for an exact numeric act identifier. It deliberately does not assert
that the act is the currently applicable revision, classify national vs local
law, write either corpus, or expose fetched text to retrieval/model context.
"""
from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Tuple
from urllib.parse import urlsplit

RT_LIVE_ADAPTER_VERSION = "V11.2.1-rt-live-adapter-1"
RT_XML_API_BASE = "https://www.riigiteataja.ee/public-api/api/v1/akt"
RT_CANONICAL_BASE = "https://www.riigiteataja.ee/akt"
RT_ALLOWED_HOSTS = frozenset({"riigiteataja.ee", "www.riigiteataja.ee"})
RT_REGISTRY_SOURCE_CANDIDATES = ("RT_NATIONAL_LAW", "RT_LOCAL_LAW")
MAX_XML_BYTES = 20 * 1024 * 1024
_ACT_ID = re.compile(r"^\d{6,14}$")
_ACT_PATH = re.compile(r"^/(?:et/)?akt/(\d{6,14})/?$")
_XML_PATH = re.compile(r"^/public-api/api/v1/akt/(\d{6,14})/xml/?$")
_ID_TAGS = frozenset({"globaalid", "globalid", "aktid", "akt_id"})
_TITLE_TAGS = ("aktinimi", "pealkiri", "title")


class RTLiveSourceError(RuntimeError):
    """The exact official-source boundary could not be verified."""


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].strip().casefold()


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _validated_rt_url(url: str, *, require_xml_path: bool = False) -> Tuple[str, str]:
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError as exc:
        raise RTLiveSourceError("Invalid Riigi Teataja URL.") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise RTLiveSourceError("Riigi Teataja source URL must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise RTLiveSourceError("Riigi Teataja source URL must not contain credentials.")
    if parsed.hostname.casefold() not in RT_ALLOWED_HOSTS:
        raise RTLiveSourceError("Riigi Teataja source URL host is not audited.")
    if parsed.query or parsed.fragment:
        raise RTLiveSourceError("Exact Riigi Teataja source URLs must not contain query or fragment parts.")
    matcher = _XML_PATH if require_xml_path else None
    match = matcher.fullmatch(parsed.path) if matcher else (_XML_PATH.fullmatch(parsed.path) or _ACT_PATH.fullmatch(parsed.path))
    if not match:
        raise RTLiveSourceError("Riigi Teataja URL is not an exact numeric act or XML API URL.")
    return match.group(1), parsed.geturl()


def extract_act_id(reference: str) -> str:
    """Return an exact numeric RT act id from a number or canonical RT URL."""
    value = str(reference or "").strip()
    if _ACT_ID.fullmatch(value):
        return value
    act_id, _ = _validated_rt_url(value)
    return act_id


def canonical_act_url(reference: str) -> str:
    return f"{RT_CANONICAL_BASE}/{extract_act_id(reference)}"


def xml_api_url(reference: str) -> str:
    return f"{RT_XML_API_BASE}/{extract_act_id(reference)}/xml"


class _RTOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validated_rt_url(newurl, require_xml_path=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _network_fetch(url: str, timeout: float, user_agent: str) -> Tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_RTOnlyRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_XML_BYTES + 1)
            final_url = response.geturl()
    except RTLiveSourceError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RTLiveSourceError(f"Riigi Teataja live fetch failed: {exc}") from exc
    if len(data) > MAX_XML_BYTES:
        raise RTLiveSourceError("Riigi Teataja XML exceeds the audited size limit.")
    return data, final_url


def _parse_verified_xml(xml_bytes: bytes, expected_act_id: str) -> Dict[str, Any]:
    if not isinstance(xml_bytes, (bytes, bytearray)) or len(xml_bytes) < 80:
        raise RTLiveSourceError("Riigi Teataja XML response is empty or implausibly short.")
    raw = bytes(xml_bytes)
    upper = raw[:8192].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RTLiveSourceError("DTD/entity declarations are not accepted in Riigi Teataja XML.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RTLiveSourceError("Riigi Teataja response is not valid XML.") from exc

    metadata_ids = []
    title = ""
    text_parts = []
    for element in root.iter():
        local = _local_name(element.tag)
        direct = _clean_text(element.text or "")
        if direct:
            text_parts.append(direct)
        if local in _ID_TAGS and direct and direct not in metadata_ids:
            metadata_ids.append(direct)
        if not title and local in _TITLE_TAGS:
            candidate = _clean_text(" ".join(element.itertext()))
            if candidate:
                title = candidate

    if not metadata_ids:
        raise RTLiveSourceError("Riigi Teataja XML does not expose an auditable act identifier.")
    if expected_act_id not in metadata_ids:
        raise RTLiveSourceError("Riigi Teataja XML act identifier does not match the requested source.")
    if not title:
        raise RTLiveSourceError("Riigi Teataja XML does not expose an auditable act title.")

    normalized_text = _clean_text(" ".join(text_parts))
    if len(normalized_text) < 40:
        raise RTLiveSourceError("Riigi Teataja XML contains too little legal text to verify.")
    return {
        "title": title,
        "xml_sha256": hashlib.sha256(raw).hexdigest(),
        "text_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        "xml_bytes": len(raw),
    }


def verify_live_rt_source(
    reference: str,
    *,
    timeout: float = 20.0,
    user_agent: str = "OigusAI/11.2.1 official-source-verifier",
    fetcher: Callable[[str, float, str], Tuple[bytes, str]] | None = None,
) -> Dict[str, Any]:
    """Fetch and verify one exact Riigi Teataja XML source.

    The result intentionally keeps authority classification and currentness
    unasserted. Later audited stages may classify the verified source and resolve
    the revision valid for a requested legal date.
    """
    act_id = extract_act_id(reference)
    expected_url = xml_api_url(act_id)
    fetch = fetcher or _network_fetch
    try:
        xml_bytes, final_url = fetch(expected_url, float(timeout), str(user_agent))
    except RTLiveSourceError:
        raise
    except Exception as exc:
        raise RTLiveSourceError(f"Riigi Teataja fetcher failed: {exc}") from exc

    final_act_id, normalized_final_url = _validated_rt_url(final_url, require_xml_path=True)
    if final_act_id != act_id:
        raise RTLiveSourceError("Riigi Teataja fetch resolved to a different act identifier.")
    if normalized_final_url.rstrip("/") != expected_url:
        raise RTLiveSourceError("Riigi Teataja fetch did not end at the canonical XML API URL.")

    parsed = _parse_verified_xml(xml_bytes, act_id)
    return {
        "version": RT_LIVE_ADAPTER_VERSION,
        "status": "OFFICIAL_SOURCE_VERIFIED",
        "source_system": "Riigi Teataja",
        "act_id": act_id,
        "canonical_url": canonical_act_url(act_id),
        "xml_url": expected_url,
        "retrieved_url": normalized_final_url,
        "title": parsed["title"],
        "xml_sha256": parsed["xml_sha256"],
        "text_sha256": parsed["text_sha256"],
        "xml_bytes": parsed["xml_bytes"],
        "authority_class": "not_asserted",
        "currentness_verified": False,
        "retrieval_enabled": False,
        "model_context_enabled": False,
        "corpus_write_enabled": False,
    }
