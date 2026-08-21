#!/usr/bin/env python3
"""Fail-closed verification for the committed V11.1 case-law corpus."""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.case_law_corpus import (
    CASE_LAW_CORPUS_PATH,
    CASE_LAW_MANIFEST_PATH,
    CaseLawCorpusError,
    verify_case_law_corpus,
)


def main() -> int:
    try:
        report = verify_case_law_corpus(
            corpus_path=PROJECT_ROOT / CASE_LAW_CORPUS_PATH,
            manifest_path=PROJECT_ROOT / CASE_LAW_MANIFEST_PATH,
        )
    except CaseLawCorpusError as exc:
        print(f"CASE-LAW CORPUS FAILED: {exc}", file=sys.stderr)
        return 2

    print("ÕigusAI V11.1 case-law corpus")
    for key, value in report.items():
        if isinstance(value, bool):
            value = str(value).lower()
        print(f"{key}: {value}")
    print("CASE-LAW CORPUS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
