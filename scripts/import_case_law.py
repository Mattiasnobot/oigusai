#!/usr/bin/env python3
"""Import a manually reviewed local JSON file into the V11.1 case-law corpus."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.case_law_corpus import (
    CASE_LAW_CORPUS_PATH,
    CASE_LAW_MANIFEST_PATH,
    CaseLawCorpusError,
    canonicalize_import_rows,
    write_corpus_and_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import locally reviewed court decisions; no network access is performed."
    )
    parser.add_argument("--input", required=True, help="Local JSON array of reviewed case-law records.")
    parser.add_argument("--corpus-output", default=CASE_LAW_CORPUS_PATH)
    parser.add_argument("--manifest-output", default=CASE_LAW_MANIFEST_PATH)
    args = parser.parse_args()

    source = Path(args.input)
    if not source.is_absolute():
        source = (PROJECT_ROOT / source).resolve()
    try:
        rows = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CaseLawCorpusError("Import input must be a JSON array.")
        records = canonicalize_import_rows(rows)
        corpus_output = Path(args.corpus_output)
        manifest_output = Path(args.manifest_output)
        if not corpus_output.is_absolute():
            corpus_output = PROJECT_ROOT / corpus_output
        if not manifest_output.is_absolute():
            manifest_output = PROJECT_ROOT / manifest_output
        manifest = write_corpus_and_manifest(
            records,
            corpus_path=corpus_output,
            manifest_path=manifest_output,
        )
    except (OSError, json.JSONDecodeError, CaseLawCorpusError) as exc:
        print(f"CASE-LAW IMPORT FAILED: {exc}", file=sys.stderr)
        return 2

    print("ÕigusAI V11.1 local curated case-law import")
    print(f"records: {manifest['record_count']}")
    print(f"corpus_sha256: {manifest['corpus_sha256']}")
    print("retrieval_enabled: false")
    print("model_context_enabled: false")
    print("live_import_enabled: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
