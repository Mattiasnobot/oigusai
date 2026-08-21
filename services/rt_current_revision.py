"""V11.4 fail-closed Riigi Teataja current-revision resolver."""
from __future__ import annotations

import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, Sequence, Tuple

from services import rt_live_source as _live
from services.rt_authority import RTAuthorityError, verify_live_rt_binding_authority

RT_SEARCH_PATH = "/api/oigusakt_otsing/1/otsi"
RT_ALLOWED_HOSTS = frozenset({"riigiteataja.ee", "www.riigiteataja.ee"})
RT_SEARCH_LIMIT = 20
MAX_SEARCH_BYTES = 5 * 1024 * 1024
_BINDING_STATUS = "BINDING_SOURCE_VERIFIED"


class RTCurrentRetrievalError(RuntimeError):
    """The requested RT revision/section cannot be verified safely."""


@dataclass(frozen=True)
class ResolvedRTRevision:
    binding: Dict[str, Any]
    xml_bytes: bytes
    official_title: str


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def key(value: Any) -> str:
    raw = str(value or "").rsplit("}", 1)[-1].strip().casefold()
    raw = raw.translate(str.maketrans({
        "õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z",
    }))
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw)


def normalize_title(value: str) -> str:
    raw = clean_text(value).casefold()
    raw = raw.translate(str.maketrans({
        "õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z",
    }))
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw).split())


def build_search_url(title: str, *, as_of: date, document_type: str) -> str:
    if as_of > date.today():
        raise RTCurrentRetrievalError("Future-date RT revision resolution is disabled.")
    doc_type = clean_text(document_type).casefold()
    if doc_type not in {"seadus", "määrus"}:
        raise RTCurrentRetrievalError(f"Unsupported RT document type: {document_type!r}.")
    query = urllib.parse.urlencode({
        "leht": "1",
        "limiit": str(RT_SEARCH_LIMIT),
        "dokument": doc_type,
        "kehtiv": as_of.isoformat(),
        "kehtivKehtetus": "false",
        "mitteJoustunud": "false",
        "pealkiri": clean_text(title),
    }, encoding="utf-8")
    return f"https://www.riigiteataja.ee{RT_SEARCH_PATH}?{query}"


def validate_search_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in RT_ALLOWED_HOSTS:
        raise RTCurrentRetrievalError("RT search must stay on the official HTTPS host.")
    if parsed.username or parsed.password or parsed.fragment or parsed.path != RT_SEARCH_PATH:
        raise RTCurrentRetrievalError("RT search URL is outside the audited endpoint.")


class _RTSearchOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_search_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def network_search_fetch(url: str, timeout: float, user_agent: str) -> Tuple[bytes, str]:
    validate_search_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "application/xml,application/json,text/xml,*/*",
    }, method="GET")
    opener = urllib.request.build_opener(_RTSearchOnlyRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            validate_search_url(final_url)
            data = response.read(MAX_SEARCH_BYTES + 1)
    except RTCurrentRetrievalError:
        raise
    except Exception as exc:
        raise RTCurrentRetrievalError(f"Riigi Teataja search failed: {exc}") from exc
    if len(data) > MAX_SEARCH_BYTES:
        raise RTCurrentRetrievalError("Riigi Teataja search response exceeded the audited size limit.")
    if not data.strip():
        raise RTCurrentRetrievalError("Riigi Teataja search returned an empty response.")
    return data, final_url


def extract_candidate_ids(payload: bytes) -> list[str]:
    text = bytes(payload).decode("utf-8", errors="replace")
    patterns = (
        r"<globaalID>\s*(\d{6,14})\s*</globaalID>",
        r"<globalId>\s*(\d{6,14})\s*</globalId>",
        r"<aktId>\s*(\d{6,14})\s*</aktId>",
        r'"globaalID"\s*:\s*"?(\d{6,14})"?',
        r'"globalId"\s*:\s*"?(\d{6,14})"?',
        r'"aktId"\s*:\s*"?(\d{6,14})"?',
        r"/public-api/api/v1/akt/(\d{6,14})/xml",
        r"/akt/(\d{6,14})(?:[/?#\"']|$)",
    )
    ids: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if match.group(1) not in ids:
                ids.append(match.group(1))
    return ids


def extract_official_title(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(bytes(xml_bytes))
    except ET.ParseError as exc:
        raise RTCurrentRetrievalError("Verified RT XML became malformed during title extraction.") from exc
    for element in root.iter():
        if key(element.tag) != "aktinimi":
            continue
        for child in element.iter():
            if key(child.tag) in {"pealkiri", "title", "nimetus"}:
                text = clean_text(" ".join(child.itertext()))
                if len(text) > 2:
                    return text
    raise RTCurrentRetrievalError("Verified RT XML is missing an explicit act title.")


class RTCurrentRevisionResolver:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        user_agent: str = "OigusAI/11.4 current-revision-resolver",
        search_fetcher: Callable[[str, float, str], Tuple[bytes, str]] | None = None,
        xml_fetcher: Callable[[str, float, str], Tuple[bytes, str]] | None = None,
        authority_verifier: Callable[..., Dict[str, Any]] = verify_live_rt_binding_authority,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.search_fetcher = search_fetcher or network_search_fetch
        self.xml_fetcher = xml_fetcher or _live._network_fetch
        self.authority_verifier = authority_verifier

    def resolve(
        self,
        title: str,
        *,
        as_of: date,
        document_types: Sequence[str] = ("seadus", "määrus"),
    ) -> ResolvedRTRevision:
        if as_of > date.today():
            raise RTCurrentRetrievalError("Future-date RT revision resolution is disabled.")
        candidate_ids: list[str] = []
        for document_type in document_types:
            url = build_search_url(title, as_of=as_of, document_type=document_type)
            try:
                payload, final_url = self.search_fetcher(url, self.timeout, self.user_agent)
            except RTCurrentRetrievalError:
                raise
            except Exception as exc:
                raise RTCurrentRetrievalError(f"Riigi Teataja search failed: {exc}") from exc
            validate_search_url(final_url)
            for act_id in extract_candidate_ids(payload):
                if act_id not in candidate_ids:
                    candidate_ids.append(act_id)
        if not candidate_ids:
            raise RTCurrentRetrievalError(f"No RT revision candidate matched title {title!r} on {as_of}.")

        exact_matches: list[ResolvedRTRevision] = []
        for act_id in candidate_ids:
            captured: Dict[str, bytes] = {}

            def capture(url: str, timeout: float, user_agent: str) -> Tuple[bytes, str]:
                data, final_url = self.xml_fetcher(url, timeout, user_agent)
                captured["xml"] = bytes(data)
                return data, final_url

            try:
                binding = self.authority_verifier(
                    act_id, as_of=as_of, timeout=self.timeout,
                    user_agent=self.user_agent, fetcher=capture,
                )
            except (RTAuthorityError, RTCurrentRetrievalError):
                continue
            except Exception:
                continue
            xml_bytes = captured.get("xml")
            if not xml_bytes or binding.get("status") != _BINDING_STATUS:
                continue
            try:
                official_title = extract_official_title(xml_bytes)
            except RTCurrentRetrievalError:
                continue
            if normalize_title(official_title) != normalize_title(title):
                continue
            exact_matches.append(ResolvedRTRevision(dict(binding), xml_bytes, official_title))

        deduped = {item.binding["act_id"]: item for item in exact_matches}
        if not deduped:
            raise RTCurrentRetrievalError(f"No exact verified RT title match for {title!r} on {as_of}.")
        if len(deduped) != 1:
            raise RTCurrentRetrievalError(
                f"Ambiguous verified RT revisions for {title!r} on {as_of}: {sorted(deduped)}."
            )
        return next(iter(deduped.values()))
