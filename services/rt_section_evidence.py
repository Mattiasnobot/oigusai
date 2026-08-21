"""V11.4 exact-section extraction from already verified RT XML bytes."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Mapping

from services.rt_current_revision import RTCurrentRetrievalError, clean_text, key


def canonical_section(value: str) -> str:
    text = re.sub(r"^§\s*", "", clean_text(value)).strip().rstrip(".")
    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    match = re.fullmatch(r"(\d+)([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", text)
    if match:
        return f"{match.group(1)}B{match.group(2).translate(superscripts)}"
    match = re.fullmatch(r"(\d+)\s*\^\s*(\d+)", text)
    if match:
        return f"{match.group(1)}B{match.group(2)}"
    match = re.fullmatch(r"(\d+)B(\d+)", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}B{match.group(2)}"
    match = re.fullmatch(r"(\d+)(?:\s*([A-Za-z]))?", text)
    if match:
        return f"{match.group(1)}{(match.group(2) or '').upper()}"
    raise RTCurrentRetrievalError(f"Unsupported RT section identifier: {value!r}.")


def _section_number(element: ET.Element) -> str | None:
    for name, value in element.attrib.items():
        if key(name) == "id":
            match = re.fullmatch(r"para([0-9]+(?:b[0-9]+|[a-z])?)", clean_text(value), re.IGNORECASE)
            if match:
                return match.group(1).upper()
    for child in element.iter():
        if child is element or key(child.tag) not in {
            "paragrahvnr", "paragrahvnumber", "kuvatavnr", "number", "nr",
        }:
            continue
        try:
            return canonical_section(clean_text(" ".join(child.itertext())))
        except RTCurrentRetrievalError:
            continue
    return None


def _section_heading(element: ET.Element) -> str:
    for child in list(element)[:12]:
        if key(child.tag) in {"pealkiri", "title", "heading", "paragrahvipealkiri"}:
            return clean_text(" ".join(child.itertext()))
    return ""


def extract_section(xml_bytes: bytes, section: str) -> Dict[str, str]:
    wanted = canonical_section(section)
    try:
        root = ET.fromstring(bytes(xml_bytes))
    except ET.ParseError as exc:
        raise RTCurrentRetrievalError("Verified RT XML became malformed during section extraction.") from exc
    matches: list[Dict[str, str]] = []
    for element in root.iter():
        if key(element.tag) not in {"paragrahv", "paragraaf", "paragraph", "section"}:
            continue
        if _section_number(element) != wanted:
            continue
        text = clean_text(" ".join(element.itertext()))
        if text:
            matches.append({"section": wanted, "heading": _section_heading(element), "text": text})
    unique = {(item["heading"], item["text"]): item for item in matches}
    if not unique:
        raise RTCurrentRetrievalError(f"Verified RT revision does not contain § {wanted}.")
    if len(unique) != 1:
        raise RTCurrentRetrievalError(f"Verified RT revision contains ambiguous duplicates for § {wanted}.")
    return next(iter(unique.values()))


def compute_section_provenance_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
