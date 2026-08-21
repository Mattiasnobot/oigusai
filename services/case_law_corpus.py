"""V11.1 curated court-practice corpus utilities.

The V11.1 layer is deliberately offline. It turns manually reviewed local JSON
records into the canonical V11.0 provenance shape and verifies the committed
corpus manifest. It does not fetch a court decision from the network and it does
not enable retrieval or model context.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlsplit

from services.case_law_provenance import (
    CASE_LAW_PROVENANCE_VERSION,
    CaseLawProvenanceVerifier,
    compute_case_law_record_sha256,
)


CASE_LAW_CORPUS_VERSION = "V11.1-case-law-corpus-1"
CASE_LAW_CORPUS_PATH = "data/case_law.json"
CASE_LAW_MANIFEST_PATH = "data/case_law_manifest.json"
ALLOWED_CASE_LAW_HOSTS = frozenset({"riigiteataja.ee", "www.riigiteataja.ee"})
ALLOWED_COURT_LEVELS = frozenset({"first_instance", "appeal", "supreme"})
_ID_COMPONENT = re.compile(r"[^A-Z0-9]+")


class CaseLawCorpusError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash corpus bytes with Git worktree CRLF normalized to canonical LF.

    Git may materialize text files with CRLF on Windows even when the importer
    serializes canonical UTF-8/LF bytes. Normalize only CRLF pairs so platform
    line endings do not create a false corpus-drift failure; every other byte
    difference remains hash-significant.
    """
    digest = hashlib.sha256()
    pending_cr = False
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if pending_cr:
                chunk = b"\r" + chunk
                pending_cr = False
            if chunk.endswith(b"\r"):
                chunk = chunk[:-1]
                pending_cr = True
            digest.update(chunk.replace(b"\r\n", b"\n"))
    if pending_cr:
        digest.update(b"\r")
    return digest.hexdigest()


def _official_rt_url(value: str) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_CASE_LAW_HOSTS
        and parsed.username is None
        and parsed.password is None
    )


def _clean_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CaseLawCorpusError(f"Case-law field is missing: {field}")
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
        raise CaseLawCorpusError(f"Case-law field contains control characters: {field}")
    return text


def _record_id(case_number: str, decision_date: str, decision_type: str) -> str:
    base = "_".join((case_number, decision_date, decision_type)).upper()
    slug = _ID_COMPONENT.sub("_", base).strip("_")
    if not slug:
        raise CaseLawCorpusError("Cannot derive case-law record ID.")
    # V11.0 IDs are capped at 125 characters including CASE_. Preserve a stable
    # human-readable prefix and add a deterministic suffix when truncation is needed.
    candidate = f"CASE_{slug}"
    if len(candidate) <= 125:
        return candidate
    suffix = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12].upper()
    return f"CASE_{slug[:107].rstrip('_')}_{suffix}"


def canonicalize_import_record(raw: Dict[str, Any], *, today: date | None = None) -> Dict[str, str]:
    """Convert one locally reviewed raw row into the V11.0 canonical record."""
    if not isinstance(raw, dict):
        raise CaseLawCorpusError("Every case-law import row must be a JSON object.")

    court_name = _clean_text(raw.get("court_name"), "court_name")
    case_number = _clean_text(raw.get("case_number"), "case_number")
    decision_date = _clean_text(raw.get("decision_date"), "decision_date")
    decision_type = _clean_text(raw.get("decision_type"), "decision_type")
    court_level = _clean_text(raw.get("court_level"), "court_level").casefold()
    canonical_url = _clean_text(raw.get("canonical_url"), "canonical_url")
    text = _clean_text(raw.get("text"), "text")

    try:
        parsed_date = date.fromisoformat(decision_date)
    except ValueError as exc:
        raise CaseLawCorpusError(f"Invalid ISO decision_date: {decision_date}") from exc
    if parsed_date.isoformat() != decision_date:
        raise CaseLawCorpusError(f"Invalid canonical decision_date: {decision_date}")
    if parsed_date > (today or date.today()):
        raise CaseLawCorpusError(f"Future case-law decision_date is not allowed: {decision_date}")
    if court_level not in ALLOWED_COURT_LEVELS:
        raise CaseLawCorpusError(
            "court_level must be one of: " + ", ".join(sorted(ALLOWED_COURT_LEVELS))
        )
    if not _official_rt_url(canonical_url):
        raise CaseLawCorpusError("canonical_url must use the official Riigi Teataja HTTPS host.")
    if len(text) < 100:
        raise CaseLawCorpusError("Case-law source text is too short for the curated corpus.")

    supplied_id = str(raw.get("id") or "").strip().upper()
    generated_id = _record_id(case_number, decision_date, decision_type)
    if supplied_id and supplied_id != generated_id:
        raise CaseLawCorpusError(
            f"Supplied case-law id does not match deterministic id: {generated_id}"
        )

    canonical: Dict[str, str] = {
        "source_kind": "case_law",
        "id": generated_id,
        "court_name": court_name,
        "case_number": case_number,
        "decision_date": decision_date,
        "decision_type": decision_type,
        "court_level": court_level,
        "canonical_url": canonical_url,
        "text": text,
    }
    canonical["record_sha256"] = compute_case_law_record_sha256(canonical)
    valid, verified = CaseLawProvenanceVerifier().verify_record(canonical)
    if not valid:
        raise CaseLawCorpusError("Canonical case-law record failed the V11.0 provenance verifier.")

    # Persist only source identity fields. authority_status/provenance_version are
    # verifier outputs so committed source data cannot self-assert legal weight.
    return {
        key: str(verified[key])
        for key in (
            "source_kind",
            "id",
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


def canonicalize_import_rows(rows: Iterable[Dict[str, Any]], *, today: date | None = None) -> List[Dict[str, str]]:
    records = [canonicalize_import_record(row, today=today) for row in rows]
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise CaseLawCorpusError("Duplicate deterministic case-law record IDs are not allowed.")
    return sorted(records, key=lambda record: record["id"])


def serialize_corpus(records: Iterable[Dict[str, str]]) -> bytes:
    return (json.dumps(list(records), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_manifest(corpus_bytes: bytes, records: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "version": CASE_LAW_CORPUS_VERSION,
        "corpus_path": CASE_LAW_CORPUS_PATH,
        "source_system": "Riigi Teataja court-decision search / court information system",
        "record_count": len(records),
        "corpus_sha256": sha256_bytes(corpus_bytes),
        "record_provenance_version": CASE_LAW_PROVENANCE_VERSION,
        "allowed_hosts": sorted(ALLOWED_CASE_LAW_HOSTS),
        "import_mode": "local_curated_json_only",
        "retrieval_enabled": False,
        "model_context_enabled": False,
        "live_import_enabled": False,
        "authority_status": "not_asserted",
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(data)
        temp = Path(handle.name)
    try:
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def write_corpus_and_manifest(
    records: List[Dict[str, str]],
    *,
    corpus_path: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    corpus_bytes = serialize_corpus(records)
    manifest = build_manifest(corpus_bytes, records)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(corpus_path, corpus_bytes)
    _atomic_write(manifest_path, manifest_bytes)
    return manifest


def verify_case_law_corpus(
    *,
    corpus_path: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = json.loads(corpus_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaseLawCorpusError(f"Required case-law corpus file is missing: {exc.filename}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseLawCorpusError(f"Invalid case-law corpus JSON: {exc}") from exc

    if not isinstance(manifest, dict) or not isinstance(records, list):
        raise CaseLawCorpusError("Case-law corpus manifest/object shape is invalid.")
    if manifest.get("version") != CASE_LAW_CORPUS_VERSION:
        raise CaseLawCorpusError("Unknown case-law corpus manifest version.")
    if manifest.get("corpus_path") != CASE_LAW_CORPUS_PATH:
        raise CaseLawCorpusError("Case-law corpus manifest path is not canonical.")
    if manifest.get("record_provenance_version") != CASE_LAW_PROVENANCE_VERSION:
        raise CaseLawCorpusError("Case-law provenance version mismatch.")
    if manifest.get("allowed_hosts") != sorted(ALLOWED_CASE_LAW_HOSTS):
        raise CaseLawCorpusError("Case-law allowed-host policy changed without a manifest version bump.")
    if manifest.get("import_mode") != "local_curated_json_only":
        raise CaseLawCorpusError("Case-law import mode is not the audited offline mode.")
    for flag in ("retrieval_enabled", "model_context_enabled", "live_import_enabled"):
        if manifest.get(flag) is not False:
            raise CaseLawCorpusError(f"V11.1 requires {flag}=false.")
    if manifest.get("authority_status") != "not_asserted":
        raise CaseLawCorpusError("Case-law corpus may not self-assert authority status.")
    if manifest.get("record_count") != len(records):
        raise CaseLawCorpusError("Case-law corpus record count does not match its manifest.")
    if manifest.get("corpus_sha256") != sha256_file(corpus_path):
        raise CaseLawCorpusError("Case-law corpus SHA-256 does not match its manifest.")

    verifier = CaseLawProvenanceVerifier()
    valid, verified = verifier.build_map(records)
    if not valid or len(verified) != len(records):
        raise CaseLawCorpusError("Case-law corpus contains an invalid or duplicate provenance record.")
    ids = [str(record.get("id") or "") for record in records]
    if ids != sorted(ids):
        raise CaseLawCorpusError("Case-law corpus records must be sorted by deterministic id.")
    for record in records:
        if not _official_rt_url(str(record.get("canonical_url") or "")):
            raise CaseLawCorpusError("Case-law corpus contains a non-official canonical URL.")

    return {
        "version": manifest["version"],
        "record_count": len(records),
        "corpus_sha256": manifest["corpus_sha256"],
        "retrieval_enabled": False,
        "model_context_enabled": False,
        "live_import_enabled": False,
        "authority_status": "not_asserted",
    }
