"""Inspect dense ranks for labelled query-evaluation cases."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.legal_search import LegalSearchService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="eval/query_cases_holdout.json")
    parser.add_argument("--id-prefix", default="CROSS-")
    parser.add_argument("--dense-limit", type=int, default=200)
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = PROJECT_ROOT / cases_path
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    settings = load_settings()
    service = LegalSearchService(data_file=settings.legal_data_file, settings=settings)
    if not service.hybrid_ready:
        raise RuntimeError(service.hybrid_status()["error"])

    for case in cases:
        case_id = str(case.get("id", ""))
        if args.id_prefix and not case_id.startswith(args.id_prefix):
            continue
        dense = service.vector_search.search(
            str(case["query"]), limit=args.dense_limit
        )
        positions = {item.law_id: rank for rank, item in enumerate(dense, start=1)}
        expected = sorted({
            str(law_id).upper()
            for group in case.get("expected_section_groups", [])
            for law_id in group
        } | {
            str(law_id).upper()
            for law_id in case.get("expected_sections_any", [])
        })
        interpretation = service.query_understanding.analyze(str(case["query"]))
        print(
            f"{case_id} domains={interpretation.domain_hints} "
            f"dense_ranks={{{', '.join(f'{law_id!r}: {positions.get(law_id)!r}' for law_id in expected)}}}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

