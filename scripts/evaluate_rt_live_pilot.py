#!/usr/bin/env python3
"""Run a privacy-preserving 20–50 case verified-live pilot through the normal API."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = PROJECT_ROOT / "eval" / "query_cases.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "rt-live-pilot-report.json"
ALLOWED_MODES = {
    "LIVE_VERIFIED",
    "MIXED_VERIFIED_AND_LOCAL",
    "LOCAL_FALLBACK",
}


def load_cases(path: Path, limit: int) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in payload if str(row.get("query") or "").strip()]
    if len(rows) < limit:
        raise RuntimeError(f"Pilot requested {limit} cases but only {len(rows)} are available.")
    return rows[:limit]


def run_pilot(args: argparse.Namespace) -> dict:
    session = requests.Session()
    access_code = os.environ.get("APP_ACCESS_CODE", "").strip()
    headers = {"X-OigusAI-Access-Code": access_code} if access_code else {}
    results = []
    modes = Counter()
    before_health = session.get(
        f"{args.base_url.rstrip('/')}/health", timeout=min(args.timeout, 30)
    )
    before_health.raise_for_status()
    before_live = before_health.json().get("verified_live_context") or {}
    started = time.perf_counter()
    for case in load_cases(Path(args.cases), args.limit):
        case_started = time.perf_counter()
        response = session.post(
            f"{args.base_url.rstrip('/')}/analyze",
            headers=headers,
            json={
                "case_description": case["query"],
                "current_message": case["query"],
                "event_date": args.as_of,
            },
            timeout=args.timeout,
        )
        elapsed_ms = round((time.perf_counter() - case_started) * 1000, 1)
        mode = "HTTP_ERROR"
        source_count = 0
        if response.ok:
            payload = response.json()
            mode = str((payload.get("legal_context") or {}).get("mode") or "MISSING")
            source_count = len(payload.get("sources_used") or [])
        modes[mode] += 1
        results.append({
            "id": str(case.get("id") or ""),
            "http_status": response.status_code,
            "legal_context_mode": mode,
            "latency_ms": elapsed_ms,
            "source_count": source_count,
        })

    total = len(results)
    after_health = session.get(
        f"{args.base_url.rstrip('/')}/health", timeout=min(args.timeout, 30)
    )
    after_health.raise_for_status()
    after_live = after_health.json().get("verified_live_context") or {}
    unexpected_errors = max(
        0,
        int(after_live.get("unexpected_error") or 0)
        - int(before_live.get("unexpected_error") or 0),
    )
    fallback_rate = modes["LOCAL_FALLBACK"] / total if total else 1.0
    http_ok = sum(row["http_status"] == 200 for row in results)
    valid_modes = sum(row["legal_context_mode"] in ALLOWED_MODES for row in results)
    acceptance = bool(
        total >= 20
        and http_ok == total
        and valid_modes == total
        and modes["LIVE_VERIFIED"] + modes["MIXED_VERIFIED_AND_LOCAL"] > 0
        and fallback_rate <= args.max_local_fallback_rate
        and unexpected_errors == 0
    )
    return {
        "version": "V11.6-live-pilot-report-1",
        "as_of": args.as_of,
        "cases": total,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "mode_counts": dict(modes),
        "local_fallback_rate": round(fallback_rate, 4),
        "max_local_fallback_rate": args.max_local_fallback_rate,
        "http_successes": http_ok,
        "valid_context_modes": valid_modes,
        "unexpected_live_errors": unexpected_errors,
        "acceptance_passed": acceptance,
        "retains_user_text": False,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--limit", type=int, default=20, choices=range(20, 51))
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--max-local-fallback-rate", type=float, default=0.25)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    if not 0.0 <= args.max_local_fallback_rate <= 1.0:
        parser.error("--max-local-fallback-rate must be between 0 and 1")
    date.fromisoformat(args.as_of)
    report = run_pilot(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    print(f"Report: {output}")
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
