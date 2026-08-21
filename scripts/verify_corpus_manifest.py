#!/usr/bin/env python3
"""Verify committed legal-corpus provenance without network access."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data/corpus_manifest.json"
class CorpusManifestError(RuntimeError): pass

def _load_json(path: Path, expected_type: type) -> Any:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc: raise CorpusManifestError(f"Missing required file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc: raise CorpusManifestError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, expected_type): raise CorpusManifestError(f"{path} must contain {expected_type.__name__}, got {type(value).__name__}.")
    return value

def _git_blob_sha(project_root: Path, relative_path: str) -> str:
    result = subprocess.run(["git","hash-object","--",relative_path], cwd=str(project_root), text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git hash-object failed").strip()
        raise CorpusManifestError(detail)
    return result.stdout.strip()

def verify_manifest(project_root: Path = PROJECT_ROOT, manifest_path: Path | None = None) -> Dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest_path = Path(manifest_path or (project_root / "data/corpus_manifest.json"))
    manifest = _load_json(manifest_path, dict)
    if manifest.get("version") != "V10.5-corpus-manifest-1": raise CorpusManifestError("Unsupported corpus manifest version.")
    corpus_rel = str(manifest.get("corpus_path") or "").strip()
    expected_blob = str(manifest.get("git_blob_sha") or "").strip().lower()
    expected_records = int(manifest.get("record_count") or 0)
    if corpus_rel != "data/laws.json" or len(expected_blob) != 40 or expected_records <= 0: raise CorpusManifestError("Corpus manifest identity fields are invalid.")
    laws = _load_json(project_root / corpus_rel, list)
    if len(laws) != expected_records: raise CorpusManifestError(f"Corpus record count drift: expected={expected_records}, actual={len(laws)}.")
    actual_blob = _git_blob_sha(project_root, corpus_rel)
    if actual_blob != expected_blob: raise CorpusManifestError(f"Corpus content drift: expected blob={expected_blob}, actual={actual_blob}.")
    legacy = project_root / "data/laws.pre_v5_1.json"
    if legacy.exists(): raise CorpusManifestError("Legacy corpus snapshot is still present: data/laws.pre_v5_1.json")
    baseline = _load_json(project_root / "eval/V61_CI_BASELINE.json", dict)
    audited = baseline.get("audited_result") or {}
    if int(audited.get("retrieval_passed") or 0) != 184: raise CorpusManifestError("Audited retrieval baseline is not 184/200.")
    provenance = baseline.get("provenance") or []
    corpus_rows = [row for row in provenance if isinstance(row, dict) and row.get("path") == corpus_rel]
    if len(corpus_rows) != 1 or corpus_rows[0].get("git_blob_sha") != expected_blob: raise CorpusManifestError("V6.1 baseline does not reference this corpus blob.")
    return {"version": manifest["version"], "corpus_path": corpus_rel, "record_count": len(laws), "git_blob_sha": actual_blob, "retrieval_baseline": "184/200", "legacy_snapshot_absent": True}

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ÕigusAI corpus manifest")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST)); args = parser.parse_args()
    try: report = verify_manifest(PROJECT_ROOT, Path(args.manifest))
    except CorpusManifestError as exc: print(f"CORPUS MANIFEST FAILED: {exc}", file=sys.stderr); return 2
    except Exception as exc: print(f"CORPUS MANIFEST ERROR: {type(exc).__name__}: {exc}", file=sys.stderr); return 3
    print("ÕigusAI corpus manifest")
    for key, value in report.items(): print(f"{key}: {value}")
    print("CORPUS MANIFEST: PASS"); return 0
if __name__ == "__main__": raise SystemExit(main())
