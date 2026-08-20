#!/usr/bin/env python3
"""Evaluate the V9.1 intake → card → urgency → retrieval workflow.

This runner intentionally stops before free-form answer generation.  It tests
the deterministic safety envelope across the audited 200-query set and reuses
the existing retrieval labels instead of manufacturing a new legal gold set.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from scripts.evaluate_queries import evaluate_case
from services.case_intake import CaseIntakeService
from services.case_workspace import CaseCardBuilder, UrgencyAnalyzer
from services.legal_search import LegalSearchService


class _OfflineIntakeModel:
    def generate_structured(self, *_args, **_kwargs):
        raise RuntimeError("deterministic evaluation")


def evaluate_workflow_case(service, intake_service, case: dict, domains: set, ids: set) -> dict:
    query = str(case.get("query") or "").strip()
    intake = intake_service.understand(query, current_message=query)
    card = CaseCardBuilder().from_intake(intake)
    urgency = UrgencyAnalyzer().analyze(query)
    retrieval = evaluate_case(service, case, domains, ids)
    clarification_text = " ".join(intake.get("clarification_questions") or []).casefold()
    checks = {
        "intake_summary": bool(str(intake.get("summary") or "").strip()),
        "case_card": card.get("revision") == 1 and bool(card.get("summary")),
        "deadline_safe": urgency.get("legal_deadline_confirmed") is False,
        "no_identifier_question": not any(
            value in clarification_text
            for value in ("isikukood", "täpne nimi", "sünniaeg", "dokumendi number")
        ),
        "retrieval": bool(retrieval.get("overall_ok")),
    }
    return {
        "id": str(case.get("id") or ""),
        "checks": checks,
        "workflow_ok": all(checks.values()),
        "retrieval": retrieval,
    }


def build_report(results: list[dict], duration_seconds: float) -> dict:
    total = len(results)
    keys = ("intake_summary", "case_card", "deadline_safe", "no_identifier_question", "retrieval")
    checks = {key: sum(bool(item["checks"].get(key)) for item in results) for key in keys}
    safety_keys = tuple(key for key in keys if key != "retrieval")
    acceptance_passed = bool(
        total
        and all(checks[key] == total for key in safety_keys)
        and checks["retrieval"] >= min(total, 184)
    )
    return {
        "version": "0.9.1",
        "cases": total,
        "workflow_passed": sum(item["workflow_ok"] for item in results),
        "checks": checks,
        "acceptance_passed": acceptance_passed,
        "retrieval_baseline_required": min(total, 184),
        "duration_seconds": round(duration_seconds, 2),
        "failures": [item["id"] for item in results if not item["workflow_ok"]],
        "method": "audited_200_query_workflow_without_free_form_generation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ÕigusAI V9.1 workflow")
    parser.add_argument("--cases", default="eval/query_cases.json")
    parser.add_argument("--json-output", default="")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("services.case_intake").setLevel(logging.ERROR)

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = PROJECT_ROOT / cases_path
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    settings = load_settings()
    service = LegalSearchService(data_file=settings.legal_data_file, settings=settings)
    intake_service = CaseIntakeService(_OfflineIntakeModel())
    domains = {str(law.get("domain") or "").upper() for law in service.laws}
    ids = {str(law.get("id") or "").upper() for law in service.laws}
    started = time.perf_counter()
    results = [
        evaluate_workflow_case(service, intake_service, case, domains, ids)
        for case in cases
    ]
    report = build_report(results, time.perf_counter() - started)
    print("ÕigusAI V9.1 workflow evaluation")
    print(f"Cases: {report['cases']}")
    print(f"Workflow pass: {report['workflow_passed']}/{report['cases']}")
    for key, passed in report["checks"].items():
        print(f"{key}: {passed}/{report['cases']}")
    if report["failures"]:
        print("Failures: " + ", ".join(report["failures"]))
    if args.json_output:
        output = Path(args.json_output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
