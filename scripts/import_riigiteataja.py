#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from pathlib import Path

from datetime import date

# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings

SETTINGS = load_settings()
RT_BASE = SETTINGS.rt_base_url
REGISTRY_FILE = SETTINGS.rt_registry_file
OUTPUT_FILE = SETTINGS.legal_data_file
CACHE_DIR = SETTINGS.rt_cache_dir
USER_AGENT = SETTINGS.rt_user_agent
TIMEOUT = SETTINGS.rt_timeout
RETRIES = SETTINGS.rt_retries
REQUEST_DELAY = SETTINGS.rt_request_delay


class CurrentActNotFound(RuntimeError):
    """Registry entry has no exact current act match in Riigi Teataja."""


# ============================================================
# ALIASES
# ============================================================

ALIAS_GROUPS = {
    "töö": {
        "töö",
        "töötaja",
        "tööandja",
        "tööleping",
        "töösuhe",
        "tööaeg",
    },

    "vallandamine": {
        "vallandamine",
        "ülesütlemine",
        "töölepingu lõpetamine",
        "töösuhte lõpetamine",
        "etteteatamine",
    },

    "üür": {
        "üür",
        "üürileping",
        "üürnik",
        "üürileandja",
        "korter",
        "eluruum",
        "eluase",
        "tagatisraha",
    },

    "perekond": {
        "perekond",
        "abielu",
        "abiellumine",
        "abikaasa",
        "lahutus",
        "laps",
        "vanem",
    },

    "laps": {
        "laps",
        "alaealine",
        "vanem",
        "hooldusõigus",
        "eestkoste",
    },

    "kuritegu": {
        "kuritegu",
        "süütegu",
        "karistus",
        "karistusõigus",
        "kurjategija",
    },

    "politsei": {
        "politsei",
        "korrakaitse",
        "ametnik",
        "kinnipidamine",
        "järelevalve",
    },

    "leping": {
        "leping",
        "lepinguline",
        "kohustus",
        "võlausaldaja",
        "võlgnik",
    },

    "kahju": {
        "kahju",
        "kahjuhüvitis",
        "hüvitis",
        "vastutus",
    },

    "maks": {
        "maks",
        "maksustamine",
        "maksumaksja",
        "maksukohustus",
        "deklaratsioon",
    },

    "andmekaitse": {
        "andmed",
        "isikuandmed",
        "andmekaitse",
        "privaatsus",
    },
}


STOPWORDS = {
    "ja",
    "ning",
    "või",
    "et",
    "on",
    "ei",
    "kui",
    "kes",
    "mis",
    "mida",
    "mille",
    "see",
    "seda",
    "selle",
    "need",
    "neid",
    "tema",
    "nende",
    "oma",
    "ka",
    "vaid",
    "aga",
    "siis",
    "üks",
    "ühe",
    "võib",
    "võivad",
    "peab",
    "peavad",
    "tuleb",
    "olema",
    "olla",
    "teha",
    "tehtud",
    "antud",
    "vastavalt",
    "käesolev",
    "käesoleva",
    "käesolevas",
    "nimetatud",
}


# ============================================================
# HTTP
# ============================================================

def fetch(url: str) -> bytes:
    """
    Download URL with retries.
    """

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/xml,"
            "text/xml,"
            "text/html,"
            "application/xhtml+xml,"
            "*/*"
        ),
    }

    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:

            request = urllib.request.Request(
                url,
                headers=headers,
            )

            with urllib.request.urlopen(
                request,
                timeout=TIMEOUT,
            ) as response:

                return response.read()

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as exc:

            last_error = exc

            if attempt < RETRIES:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(
        f"Failed to download {url}: {last_error}"
    )


# ============================================================
# CACHE
# ============================================================

def cached_fetch(
    url: str,
    force: bool = False,
) -> bytes:

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_key = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()

    cache_file = CACHE_DIR / f"{cache_key}.bin"

    if cache_file.exists() and not force:

        logging.debug(
            "CACHE HIT: %s",
            url,
        )

        return cache_file.read_bytes()

    logging.debug(
        "DOWNLOAD: %s",
        url,
    )

    data = fetch(url)

    tmp = cache_file.with_suffix(".tmp")

    tmp.write_bytes(data)

    tmp.replace(cache_file)

    return data


# ============================================================
# TEXT HELPERS
# ============================================================

def local_name(tag: str) -> str:

    return tag.rsplit(
        "}",
        1,
    )[-1].lower()


def clean_text(text: str) -> str:

    text = html.unescape(
        text or ""
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n[ \t]+",
        "\n",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def element_text(
    element: ET.Element,
) -> str:

    parts = []

    for value in element.itertext():

        if value and value.strip():
            parts.append(value)

    return clean_text(
        " ".join(parts)
    )


# ============================================================
# ACT ID RESOLUTION
# ============================================================

def normalize_title(value: str) -> str:
    """Normalize Estonian act titles for conservative exact matching."""
    value = (value or "").casefold()
    value = value.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u", "õ": "o", "š": "s", "ž": "z"}))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def safe_url(url: str) -> str:
    """
    Convert Unicode characters in the URL path to percent-encoded UTF-8.

    Example:
        VÕS -> V%C3%95S
    """

    parsed = urllib.parse.urlsplit(url)

    path = urllib.parse.quote(
        parsed.path,
        safe="/:@-._~!$&'()*+,;=",
    )

    query = urllib.parse.quote(
        parsed.query,
        safe="=&?/:;+,$",
    )

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            query,
            parsed.fragment,
        )
    )


def title_search_variants(name: str) -> list[str]:
    """Return conservative RT title-query variants.

    The public search currently treats conjunctions in some hyphenated Estonian
    act titles as mandatory search terms even though they are absent from its
    title index. Candidate XML is still verified against the exact official
    registry title, so this broader lookup cannot silently accept another act.
    """
    original = clean_text(name)
    simplified = re.sub(r"[-–—]+", " ", original)
    simplified = " ".join(
        token
        for token in simplified.split()
        if token.casefold() not in {"ja", "ning"}
    )
    return list(dict.fromkeys(value for value in (original, simplified) if value))


def find_current_act_id(
    prefix: str,
    name: str,
) -> str:
    """
    Find the current Riigi Teataja act ID through
    the public search API.

    The search API is preferable to scraping the
    HTML page because the new RT frontend is dynamic.
    """

    today = date.today().isoformat()

    base_params = {
        "leht": "1",
        "limiit": "20",

        # We are interested in laws.
        "dokument": "seadus",

        # Current version.
        "kehtiv": today,

        "kehtivKehtetus": "false",
        "mitteJoustunud": "false",

    }

    # --------------------------------------------------------
    # Look for an act ID.
    #
    # The API has historically returned XML/structured
    # responses. We deliberately support several layouts.
    # --------------------------------------------------------

    patterns = [
        # Riigi Teataja uses globaalID as the stable numeric identifier
        # in act metadata/search payloads. Keep older/alternate names too.
        r"<globaalID>\s*(\d+)\s*</globaalID>",
        r"<globalId>\s*(\d+)\s*</globalId>",
        r"<aktId>\s*(\d+)\s*</aktId>",
        r"<akt_id>\s*(\d+)\s*</akt_id>",
        r'"globaalID"\s*:\s*"?(\d+)"?',
        r'"globalId"\s*:\s*"?(\d+)"?',
        r'"aktId"\s*:\s*"?(\d+)"?',
        r'"akt_id"\s*:\s*"?(\d+)"?',
        r"/public-api/api/v1/akt/(\d+)/xml",
        r"/akt/(\d{6,14})",
    ]

    ids = []

    for title_variant in title_search_variants(name):
        params = dict(base_params)
        params["pealkiri"] = title_variant
        query = urllib.parse.urlencode(
            params,
            encoding="utf-8",
        )
        url = (
            f"{RT_BASE}"
            f"/api/oigusakt_otsing/1/otsi?"
            f"{query}"
        )
        logging.debug("RT search: %s", url)
        data = cached_fetch(url, force=False)
        text = data.decode("utf-8", errors="replace")

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value = match.group(1)
                if value not in ids:
                    ids.append(value)
        if ids:
            break

    if not ids:

        raise CurrentActNotFound(
            f"No current RT act matched title {name!r} ({prefix})"
        )

    # --------------------------------------------------------
    # Verify candidates by downloading their XML.
    #
    # This is important because the search can return
    # related acts.
    # --------------------------------------------------------

    for act_id in ids:

        xml_url = (
            f"{RT_BASE}"
            f"/public-api/api/v1/akt/"
            f"{act_id}/xml"
        )

        try:

            xml_data = cached_fetch(
                xml_url,
                force=False,
            )

            try:
                root = parse_xml(xml_data)
            except RuntimeError:
                # A previously interrupted download must not poison future
                # imports. Refresh once, then fail closed if XML is still bad.
                xml_data = cached_fetch(xml_url, force=True)
                root = parse_xml(xml_data)

            title = extract_law_name(
                root,
                name,
            )

            normalized_title = normalize_title(title)
            normalized_name = normalize_title(name)

            # Exact normalized title match only. A title-filter search may also
            # return amendment acts; accepting a partial match could import the
            # wrong document into a legal corpus.
            if normalized_title == normalized_name:
                return act_id

        except Exception as exc:

            logging.debug(
                "Could not verify act %s: %s",
                act_id,
                exc,
            )

    raise CurrentActNotFound(
        f"No exact current RT title match for {name!r} ({prefix}); "
        f"candidates checked: {len(ids)}"
    )


def resolve_xml_url(
    registry_url: str,
    prefix: str,
    name: str,
    force: bool = False,
) -> str:
    """
    Resolve registry entry to the new 2026 XML API URL.
    """

    # First try the API search.
    api_not_found = None
    try:

        act_id = find_current_act_id(
            prefix,
            name,
        )

        return (
            f"{RT_BASE}"
            f"/public-api/api/v1/akt/"
            f"{act_id}/xml"
        )

    except CurrentActNotFound as exc:
        api_not_found = exc
        logging.debug("No current API title match for %s: %s", prefix, exc)
    except Exception as exc:

        logging.debug(
            "API lookup failed for %s: %s",
            prefix,
            exc,
        )

    # --------------------------------------------------------
    # Fallback: resolve the registry shortcut by following HTTP
    # redirects. The 2026 RT frontend is JavaScript-driven, so
    # scraping the returned HTML for an act id is unreliable.
    # --------------------------------------------------------

    safe_registry_url = safe_url(registry_url)
    page_url = safe_registry_url

    if "leiaKehtiv" not in page_url:
        separator = "&" if "?" in page_url else "?"
        page_url += separator + "leiaKehtiv"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*",
    }

    try:
        request = urllib.request.Request(page_url, headers=headers)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            final_url = response.geturl()
            page = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Could not resolve RT shortcut for {prefix} ({name}): {exc}"
        ) from exc

    candidates = [final_url, page.decode("utf-8", errors="replace")]
    patterns = [
        r"/public-api/api/v1/akt/(\d+)/xml",
        r"/akt/(\d{6,14})(?:[/?#]|$)",
        r"<globaalID>\s*(\d+)\s*</globaalID>",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate, re.IGNORECASE)
            if match:
                act_id = match.group(1)
                return (
                    f"{RT_BASE}"
                    f"/public-api/api/v1/akt/"
                    f"{act_id}/xml"
                )

    if api_not_found is not None:
        raise CurrentActNotFound(str(api_not_found))

    raise RuntimeError(
        f"Could not resolve current act for {prefix} ({name}); "
        f"RT shortcut ended at {final_url!r} without a numeric act id"
    )


# ============================================================
# XML
# ============================================================

def parse_xml(
    xml_data: bytes,
) -> ET.Element:

    try:

        return ET.fromstring(
            xml_data
        )

    except ET.ParseError as exc:

        raise RuntimeError(
            f"Invalid XML: {exc}"
        ) from exc


def get_attribute(
    element: ET.Element,
    *names: str,
) -> str | None:

    wanted = {
        name.lower()
        for name in names
    }

    for key, value in element.attrib.items():

        if key.lower() in wanted:

            return clean_text(value)

    return None


# ============================================================
# SECTION EXTRACTION
# ============================================================

def _canonical_section_from_text(value: str) -> str | None:
    """Convert a displayed section number to a citation-safe key.

    Examples: 54a -> 54A, 3² -> 3B2. Riigi Teataja uses `b` in URL
    anchors for superscript section numbers (for example para3b2).
    """
    text = clean_text(value or "")
    text = re.sub(r"^\s*§\s*", "", text)
    text = text.strip().rstrip(".")

    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    match = re.fullmatch(r"(\d+)([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", text)
    if match:
        return f"{match.group(1)}B{match.group(2).translate(superscripts)}"

    # Common textual forms: 3^2 / 3²-like data normalized upstream.
    match = re.fullmatch(r"(\d+)\s*\^\s*(\d+)", text)
    if match:
        return f"{match.group(1)}B{match.group(2)}"

    match = re.search(r"(\d+(?:[A-Za-z])?)", text)
    if match:
        return match.group(1).upper()
    return None


def find_section_number(
    element: ET.Element,
) -> str | None:
    # The RT document anchor is the most reliable representation and already
    # encodes superscript sections as e.g. para3b2.
    element_id = get_attribute(element, "id") or ""
    anchor_match = re.fullmatch(r"para([0-9]+(?:b[0-9]+|[a-z])?)", element_id, re.IGNORECASE)
    if anchor_match:
        return anchor_match.group(1).upper()

    value = get_attribute(
        element,
        "nr",
        "number",
        "num",
        "paragrahv",
    )
    if value:
        parsed = _canonical_section_from_text(value)
        if parsed:
            return parsed

    # Current RT XML uses <paragrahvNr> and <kuvatavNr>. Search descendants,
    # because namespaces and wrapper nodes vary between schemas/versions.
    preferred_tags = {
        "paragrahvnr",
        "paragrahvnumber",
        "kuvatavnr",
        "number",
        "nr",
    }
    for child in element.iter():
        if child is element:
            continue
        if local_name(child.tag) not in preferred_tags:
            continue
        parsed = _canonical_section_from_text(element_text(child))
        if parsed:
            return parsed

    return None


def find_section_heading(
    element: ET.Element,
) -> str:

    for child in list(element)[:10]:

        tag = local_name(
            child.tag
        )

        if tag not in {
            "pealkiri",
            "title",
            "heading",
            "paragrahvipealkiri",
        }:
            continue

        text = element_text(child)

        if text:
            return text

    return ""


def extract_sections(
    root: ET.Element,
) -> list[tuple[str, str, str]]:

    candidates = []

    section_tags = {
        "paragrahv",
        "paragraaf",
        "paragraph",
        "section",
        "säte",
        "sate",
        "norm",
    }

    for element in root.iter():

        if local_name(
            element.tag
        ) not in section_tags:

            continue

        number = find_section_number(
            element
        )

        if not number:
            continue

        text = element_text(
            element
        )

        if not text:
            continue

        heading = find_section_heading(
            element
        )

        candidates.append(
            (
                number,
                heading,
                text,
            )
        )

    # Fallback if the XML schema uses no explicit
    # <paragrahv> element.
    if not candidates:

        for element in root.iter():

            text = element_text(
                element
            )

            match = re.match(
                r"^§\s*"
                r"(\d+(?:\s*[a-z])?)"
                r"\.?\s*"
                r"(.*)",
                text,
                re.IGNORECASE | re.DOTALL,
            )

            if not match:
                continue

            number = re.sub(
                r"\s+",
                "",
                match.group(1),
            )

            body = clean_text(
                match.group(2)
            )

            if body:
                candidates.append(
                    (
                        number,
                        "",
                        body,
                    )
                )

    # Deduplicate.
    result = {}

    for number, heading, text in candidates:

        current = result.get(
            number
        )

        if current is None:

            result[number] = (
                number,
                heading,
                text,
            )

        elif len(text) > len(
            current[2]
        ):

            result[number] = (
                number,
                heading,
                text,
            )

    def sort_key(item):

        number = item[0]

        match = re.match(
            r"(\d+)(.*)",
            number,
        )

        if not match:
            return (
                999999,
                number,
            )

        return (
            int(match.group(1)),
            match.group(2),
        )

    return sorted(
        result.values(),
        key=sort_key,
    )


# ============================================================
# ALIASES
# ============================================================

def words(
    text: str,
) -> list[str]:

    return re.findall(
        r"[A-Za-zÕÄÖÜõäöüŠŽšž0-9-]{3,}",
        text.lower(),
    )


def generate_aliases(
    text: str,
    law_name: str,
) -> list[str]:

    lower = text.lower()

    aliases = set()

    # Semantic/legal synonym groups.
    for trigger, group in ALIAS_GROUPS.items():

        if trigger in lower:

            aliases.update(
                group
            )

    # Important words appearing repeatedly.
    frequencies = {}

    for word in words(text):

        if word in STOPWORDS:
            continue

        frequencies[word] = (
            frequencies.get(word, 0)
            + 1
        )

    frequent = sorted(
        frequencies.items(),
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )

    for word, _count in frequent[:12]:

        if len(word) >= 4:

            aliases.add(
                word
            )

    # Law name terms.
    for word in words(law_name):

        if word not in STOPWORDS:

            aliases.add(
                word
            )

    return sorted(
        aliases,
        key=lambda value: (
            len(value),
            value,
        ),
    )[:25]


# ============================================================
# LAW TITLE
# ============================================================

def extract_law_name(
    root: ET.Element,
    fallback: str,
) -> str:
    # Current RT XML stores the act title under <aktinimi><nimi><pealkiri>.
    # Restrict the first pass to that metadata subtree so a chapter/section
    # heading cannot be mistaken for the name of the act.
    for element in root.iter():
        if local_name(element.tag) != "aktinimi":
            continue
        for child in element.iter():
            if local_name(child.tag) in {"pealkiri", "title", "nimetus"}:
                text = element_text(child)
                if len(text) > 2:
                    return text

    possible_tags = {
        "pealkiri",
        "title",
        "aktinimetus",
        "nimetus",
    }
    for element in root.iter():
        if local_name(element.tag) in possible_tags:
            text = element_text(element)
            if len(text) > 5:
                return text
    return fallback


# ============================================================
# RECORD
# ============================================================

def make_record(
    prefix: str,
    law_name: str,
    registry_url: str,
    section: tuple[str, str, str],
) -> dict:

    prefix = prefix.strip().upper()
    number, heading, text = section

    title = (
        f"{law_name} § {number}"
    )

    if heading:

        title += (
            f" – {heading}"
        )

    content_hash = hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    return {
        "id": f"{prefix}_{number}",

        "title": title,

        "text": text,

        "source": (
            f"Riigi Teataja: "
            f"{prefix}"
        ),

        "domain": prefix,

        "law_name": law_name,

        "section": number,

        "aliases": generate_aliases(
            text,
            law_name,
        ),

        "url": (
            registry_url
            + (
                "&"
                if "?" in registry_url
                else "?"
            )
            + f"leiaKehtiv#para{number.lower()}"
        ),

        "content_hash": content_hash,
    }


# ============================================================
# REGISTRY
# ============================================================

def load_registry() -> list[dict]:

    if not REGISTRY_FILE.exists():

        raise FileNotFoundError(
            f"Registry not found: "
            f"{REGISTRY_FILE}"
        )

    data = json.loads(
        REGISTRY_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        data,
        list,
    ):

        raise ValueError(
            "rt_registry.json must "
            "contain an array"
        )

    result = []

    seen = set()

    for item in data:

        if not all(
            isinstance(
                item.get(field),
                str,
            )
            and item[field].strip()
            for field in (
                "prefix",
                "name",
                "url",
            )
        ):

            logging.warning(
                "Skipping invalid registry entry: %r",
                item,
            )

            continue

        prefix = item[
            "prefix"
        ].strip().upper()

        if prefix in seen:

            logging.warning(
                "Duplicate prefix: %s",
                prefix,
            )

            continue

        seen.add(prefix)

        normalized_item = dict(item)
        normalized_item["prefix"] = prefix
        result.append(
            normalized_item
        )

    return result


# ============================================================
# VALIDATION
# ============================================================

def validate(
    records: list[dict],
) -> None:

    if not records:
        raise ValueError("Import produced zero legal sections")

    required = {
        "id",
        "title",
        "text",
        "source",
        "domain",
        "law_name",
        "section",
        "aliases",
        "url",
        "content_hash",
    }

    ids = set()

    for record in records:

        missing = (
            required
            - set(record)
        )

        if missing:

            raise ValueError(
                f"{record.get('id')}: "
                f"missing {missing}"
            )

        if record["id"] in ids:

            raise ValueError(
                f"Duplicate ID: "
                f"{record['id']}"
            )

        ids.add(
            record["id"]
        )

        if not record[
            "text"
        ].strip():

            raise ValueError(
                f"Empty text: "
                f"{record['id']}"
            )

        if not isinstance(
            record["aliases"],
            list,
        ):

            raise ValueError(
                f"Invalid aliases: "
                f"{record['id']}"
            )


# ============================================================
# ATOMIC WRITE
# ============================================================

def save_json(
    path: Path,
    data,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(
        path
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Import Estonian laws "
            "from Riigi Teataja"
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "Only process first N "
            "laws"
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore cached downloads"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Do not write laws.json"
        ),
    )

    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Write a partial corpus even if one or more laws fail. "
            "Default is fail-closed: keep the previous laws.json unchanged."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=(
            logging.DEBUG
            if args.verbose
            else logging.INFO
        ),
        format=(
            "%(levelname)s: "
            "%(message)s"
        ),
    )

    registry = load_registry()

    if args.limit > 0:

        registry = registry[
            :args.limit
        ]

    all_records = []

    failed = []
    skipped = []
    failure_reasons = Counter()

    acts_imported = 0

    logging.info(
        "Registry entries: %d",
        len(registry),
    )

    # --------------------------------------------------------
    # Import every registered law.
    # --------------------------------------------------------

    for index, item in enumerate(
        registry,
        start=1,
    ):

        prefix = item[
            "prefix"
        ]

        name = item[
            "name"
        ]

        registry_url = item[
            "url"
        ]

        logging.info(
            "[%d/%d] %s — %s",
            index,
            len(registry),
            prefix,
            name,
        )

        try:

            xml_url = resolve_xml_url(
                registry_url,
                prefix,
                name,
                force=args.force,
            )

            logging.debug(
                "XML URL: %s",
                xml_url,
            )

            xml_data = cached_fetch(
                xml_url,
                force=args.force,
            )

            root = parse_xml(
                xml_data
            )

            actual_name = (
                extract_law_name(
                    root,
                    name,
                )
            )

            sections = extract_sections(
                root
            )

            if not sections:

                raise RuntimeError(
                    "No § sections found"
                )

            acts_imported += 1

            for section in sections:

                record = make_record(
                    prefix,
                    actual_name,
                    registry_url,
                    section,
                )

                all_records.append(
                    record
                )

            logging.info(
                "  -> %d sections",
                len(sections),
            )

        except CurrentActNotFound as exc:
            skipped.append(prefix)
            logging.warning("  SKIPPED (no exact current act): %s", exc)

        except Exception as exc:

            failed.append(prefix)
            failure_reasons[str(exc)] += 1

            logging.error(
                "  FAILED: %s",
                exc,
            )

        if REQUEST_DELAY > 0 and index < len(registry):
            time.sleep(REQUEST_DELAY)

    # --------------------------------------------------------
    # Stable ordering.
    # --------------------------------------------------------

    def sort_key(record):

        section = record[
            "section"
        ]

        match = re.match(
            r"(\d+)(.*)",
            section,
        )

        if match:

            section_number = int(
                match.group(1)
            )

            suffix = match.group(2)

        else:

            section_number = 999999

            suffix = section

        return (
            record[
                "domain"
            ].lower(),

            section_number,

            suffix,
        )

    all_records.sort(
        key=sort_key
    )

    summary = {
        "registry_entries": len(
            registry
        ),
        "acts_imported": (
            acts_imported
        ),
        "sections": len(
            all_records
        ),
        "failed": failed,
        "skipped_not_current": skipped,
    }

    print()
    print(
        "========================================"
    )
    print(
        " Riigi Teataja import"
    )
    print(
        "========================================"
    )

    print(
        f"Registry: {len(registry)}"
    )

    print(
        f"Acts imported: {acts_imported}"
    )

    print(
        f"Sections: {len(all_records)}"
    )

    print(
        f"Failed: {len(failed)}"
    )

    print(
        f"Skipped (no exact current act): {len(skipped)}"
    )

    if skipped:
        print("Skipped laws:", ", ".join(skipped))

    if failed:

        print(
            "Failed laws:",
            ", ".join(failed),
        )

        print("\nMost common failure reasons:")
        for reason, count in failure_reasons.most_common(5):
            print(f"  {count}x {reason}")

    # --------------------------------------------------------
    # Fail closed on partial imports unless explicitly allowed.
    # --------------------------------------------------------

    if failed and not args.allow_partial:
        print(
            "\nImport was incomplete; existing laws.json was NOT modified. "
            "Use --allow-partial only for deliberate development/testing."
        )
        return 2

    # --------------------------------------------------------
    # Validation.
    # --------------------------------------------------------

    try:
        validate(all_records)
    except ValueError as exc:
        logging.error("Import validation failed: %s", exc)
        print("\nImport validation failed; existing laws.json was NOT modified.")
        return 2

    # --------------------------------------------------------
    # Dry run.
    # --------------------------------------------------------

    if args.dry_run:

        print(
            "\nDry run — laws.json "
            "was NOT modified."
        )

        return (
            0
            if not failed
            else 2
        )

    # --------------------------------------------------------
    # Write.
    # --------------------------------------------------------

    save_json(
        OUTPUT_FILE,
        all_records,
    )

    print()
    print(
        f"Written: {OUTPUT_FILE}"
    )

    return (
        0
        if not failed
        else 2
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
