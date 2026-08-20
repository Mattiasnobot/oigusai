#!/usr/bin/env python3
"""Evaluate OigusAI retrieval against the audited query set.

The evaluator supports both the original V5 domain-only cases and the richer
200-case schema with expected behaviours, section labels and multi-law groups.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.legal_search import HistoricalDataUnavailableError, LegalSearchService


def _upper_set(values: Iterable[Any] | None) -> set[str]:
    return {str(value).upper() for value in (values or []) if str(value).strip()}


def _case_tags(case: dict[str, Any]) -> list[str]:
    tags = case.get("tags")
    if isinstance(tags, list) and tags:
        return [str(tag) for tag in tags]
    return [str(case.get("tag", "other"))]


def select_cases(
    cases: list[dict[str, Any]], requested_ids: Iterable[Any] | None = None
) -> list[dict[str, Any]]:
    """Return cases in dataset order, optionally restricted to known IDs."""
    requested = {str(value) for value in (requested_ids or []) if str(value)}
    if not requested:
        return cases
    cases_by_id = {str(case.get("id", "")): case for case in cases}
    missing_ids = sorted(requested - set(cases_by_id))
    if missing_ids:
        raise ValueError("Unknown evaluation case IDs: " + ", ".join(missing_ids))
    return [case for case in cases if str(case.get("id", "")) in requested]


def _all_true(checks: dict[str, bool | None]) -> bool:
    applicable = [value for value in checks.values() if value is not None]
    return all(applicable) if applicable else True


def evaluate_case(
    service: LegalSearchService,
    case: dict[str, Any],
    available_domains: set[str] | None = None,
    available_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one case and return a serialisable, inspectable result."""
    expected_behavior = str(case.get("expected_behavior", "retrieve"))
    interpretation_notes: list[str] = []
    error = ""
    started = time.perf_counter()

    try:
        laws, interpretation = service.search_laws_with_context(
            str(case["query"]), str(case.get("event_date", ""))
        )
        interpretation_notes = list(getattr(interpretation, "notes", []) or [])
        actual_behavior = "retrieve" if laws else "no_result"
    except HistoricalDataUnavailableError as exc:
        laws = []
        actual_behavior = "historical_unavailable"
        error = str(exc)

    returned_domains = [str(law.get("domain", "")).upper() for law in laws]
    returned_ids = [str(law.get("id", "")).upper() for law in laws]
    returned_domain_set = set(returned_domains)
    returned_id_set = set(returned_ids)

    expected_domains_any = _upper_set(case.get("expected_domains"))
    expected_domains_all = _upper_set(case.get("expected_domains_all"))
    expected_sections_any = _upper_set(case.get("expected_sections_any"))
    expected_section_groups = [
        _upper_set(group) for group in case.get("expected_section_groups", [])
    ]

    checks: dict[str, bool | None] = {
        "domain_any": (
            bool(expected_domains_any & returned_domain_set)
            if expected_domains_any
            else None
        ),
        "domain_all": (
            expected_domains_all <= returned_domain_set
            if expected_domains_all
            else None
        ),
        "section_any": (
            bool(expected_sections_any & returned_id_set)
            if expected_sections_any
            else None
        ),
        "section_groups": (
            all(group & returned_id_set for group in expected_section_groups)
            if expected_section_groups
            else None
        ),
    }
    if expected_behavior != "retrieve":
        # Refusal and historical-safety cases measure behaviour only. Their
        # section labels document the topic but are not retrieval targets.
        checks = {name: None for name in checks}

    behavior_ok = actual_behavior == expected_behavior
    retrieval_ok = _all_true(checks) if expected_behavior == "retrieve" else True
    overall_ok = behavior_ok and retrieval_ok

    corpus_gap = False
    if expected_behavior == "retrieve":
        if available_ids is not None and (
            expected_sections_any or expected_section_groups
        ):
            labelled = set(expected_sections_any)
            for group in expected_section_groups:
                labelled.update(group)
            corpus_gap = not bool(labelled & available_ids)
        elif available_domains is not None and (
            expected_domains_any or expected_domains_all
        ):
            expected = expected_domains_any | expected_domains_all
            corpus_gap = not bool(expected & available_domains)

    return {
        "id": str(case.get("id", "")),
        "query": str(case["query"]),
        "split": str(case.get("split", "legacy")),
        "tags": _case_tags(case),
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "behavior_ok": behavior_ok,
        "checks": checks,
        "overall_ok": overall_ok,
        "corpus_gap": corpus_gap,
        "expected_domains_any": sorted(expected_domains_any),
        "expected_domains_all": sorted(expected_domains_all),
        "expected_sections_any": sorted(expected_sections_any),
        "expected_section_groups": [sorted(group) for group in expected_section_groups],
        "returned_domains": returned_domains,
        "returned_ids": returned_ids,
        "interpretation_notes": interpretation_notes,
        "error": error,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _ratio(passed: int, total: int) -> str:
    percentage = passed / total if total else 0.0
    return f"{passed}/{total} = {percentage:.1%}"


def _record_group(
    totals: Counter[str], passed: Counter[str], key: str, ok: bool
) -> None:
    totals[key] += 1
    if ok:
        passed[key] += 1


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def print_report(
    cases_path: Path,
    results: list[dict[str, Any]],
    max_results: int,
    show_failures: bool = False,
) -> None:
    overall_passed = sum(result["overall_ok"] for result in results)
    behavior_passed = sum(result["behavior_ok"] for result in results)
    corpus_gaps = sum(result["corpus_gap"] for result in results)
    observed = Counter(result["actual_behavior"] for result in results)
    latencies = [float(result["latency_ms"]) for result in results]

    metric_totals: Counter[str] = Counter()
    metric_passed: Counter[str] = Counter()
    split_totals: Counter[str] = Counter()
    split_passed: Counter[str] = Counter()
    tag_totals: Counter[str] = Counter()
    tag_passed: Counter[str] = Counter()

    for result in results:
        _record_group(
            split_totals, split_passed, result["split"], result["overall_ok"]
        )
        for tag in result["tags"]:
            _record_group(tag_totals, tag_passed, tag, result["overall_ok"])
        for name, value in result["checks"].items():
            if value is not None:
                _record_group(metric_totals, metric_passed, name, value)

    print("OigusAI query evaluation")
    print(f"Dataset: {cases_path}")
    print(f"Cases: {len(results)}")
    print(f"Overall pass: {_ratio(overall_passed, len(results))}")
    print(f"Expected-behaviour accuracy: {_ratio(behavior_passed, len(results))}")
    labels = {
        "domain_any": f"Domain-any Recall@{max_results}",
        "domain_all": f"Domain-all Recall@{max_results}",
        "section_any": f"Section-any Recall@{max_results}",
        "section_groups": f"Section-group Recall@{max_results}",
    }
    for name in ("domain_any", "domain_all", "section_any", "section_groups"):
        if metric_totals[name]:
            print(f"{labels[name]}: {_ratio(metric_passed[name], metric_totals[name])}")
    print(f"Corpus-gap rate: {_ratio(corpus_gaps, len(results))}")
    print(
        "Retrieval latency: "
        f"p50={_percentile(latencies, 0.50):.1f} ms, "
        f"p95={_percentile(latencies, 0.95):.1f} ms, "
        f"max={max(latencies, default=0.0):.1f} ms"
    )
    print(
        "Observed behaviours: "
        + ", ".join(f"{name}={count}" for name, count in sorted(observed.items()))
    )

    print("By split:")
    for split in sorted(split_totals):
        print(f"  {split}: {_ratio(split_passed[split], split_totals[split])}")

    print("By tag:")
    for tag in sorted(tag_totals):
        print(f"  {tag}: {_ratio(tag_passed[tag], tag_totals[tag])}")

    failures = [result for result in results if not result["overall_ok"]]
    if failures and show_failures:
        print("\nFailures:")
        for result in failures:
            print(f"- {result['id']} [{result['split']}]: {result['query']}")
            print(
                f"  behavior={result['actual_behavior']} "
                f"expected={result['expected_behavior']} checks={result['checks']}"
            )
            print(
                f"  returned_ids={result['returned_ids']} "
                f"returned_domains={result['returned_domains']}"
            )
            if result["corpus_gap"]:
                print("  corpus_gap=true")
            if result["interpretation_notes"]:
                print(f"  interpretation={result['interpretation_notes']}")
            if result["error"]:
                print(f"  error={result['error']}")


def main() -> int:
    # Windows terminals commonly default to cp1252, which cannot render arrows
    # and several Estonian evaluation notes. Keep the report readable instead
    # of aborting after the evaluation has already completed.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Evaluate OigusAI query retrieval")
    parser.add_argument("--cases", default="eval/query_cases.json")
    parser.add_argument("--show-failures", action="store_true")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Evaluate only this case ID; repeat the option for multiple cases.",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = PROJECT_ROOT / cases_path
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Evaluation file must contain a JSON list")
    cases = select_cases(cases, args.case_id)

    settings = load_settings()
    service = LegalSearchService(data_file=settings.legal_data_file, settings=settings)
    available_domains = {
        str(law.get("domain", "")).upper() for law in service.laws
    }
    available_ids = {str(law.get("id", "")).upper() for law in service.laws}
    results = [
        evaluate_case(service, case, available_domains, available_ids)
        for case in cases
    ]
    print_report(cases_path, results, settings.legal_max_results, args.show_failures)
    return 0 if all(result["overall_ok"] for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
