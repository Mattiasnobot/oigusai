"""Inspect labelled section positions before or after the V6.1 reranker."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
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
    parser.add_argument("--reranker", action="store_true")
    parser.add_argument("--candidates", type=int, default=20)
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = PROJECT_ROOT / cases_path
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    base = load_settings()
    settings = replace(
        base,
        legal_max_results=100,
        reranker_enabled=args.reranker,
        reranker_candidates=args.candidates,
    )
    service = LegalSearchService(data_file=settings.legal_data_file, settings=settings)

    for case in cases:
        case_id = str(case.get("id", ""))
        if args.id_prefix and not case_id.startswith(args.id_prefix):
            continue
        laws = service.search_laws(str(case["query"]), str(case.get("event_date", "")))
        returned = [law["id"] for law in laws]
        positions = {law_id: rank for rank, law_id in enumerate(returned, start=1)}
        expected = sorted({
            str(law_id).upper()
            for group in case.get("expected_section_groups", [])
            for law_id in group
        } | {
            str(law_id).upper()
            for law_id in case.get("expected_sections_any", [])
        })
        print(
            f"{case_id} top5={returned[:5]} "
            f"expected_ranks={{{', '.join(f'{law_id!r}: {positions.get(law_id)!r}' for law_id in expected)}}}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
