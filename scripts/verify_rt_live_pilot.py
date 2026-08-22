#!/usr/bin/env python3
"""Deterministic CI contract for V11.6 live-pilot observability."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "rt_live_pilot_manifest.json"
VERSION = "V11.6-live-pilot-observability-1"


def main() -> int:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {
        "version": VERSION,
        "depends_on_runtime_version": "V11.5.1-verified-live-runtime-1",
        "runtime_default_enabled": False,
        "minimum_pilot_cases": 20,
        "maximum_pilot_cases": 50,
        "api_exposes_legal_context": True,
        "pipeline_exposes_legal_context_mode": True,
        "health_exposes_aggregate_live_metrics": True,
        "admin_exposes_aggregate_live_metrics": True,
        "ui_exposes_source_freshness": True,
        "retains_user_text": False,
        "pilot_report_retains_user_text": False,
        "unexpected_errors_must_be_zero": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"V11.6 manifest drift: {key}")
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "services" / "analysis_orchestrator.py").read_text(encoding="utf-8")
    chat = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    evaluator = (ROOT / "scripts" / "evaluate_rt_live_pilot.py").read_text(encoding="utf-8")
    required_markers = (
        (main_text, '"verified_live_context"'),
        (main_text, 'legal_context=finalized["legal_context"]'),
        (orchestrator, "legal_context_mode="),
        (orchestrator, '"MIXED_VERIFIED_AND_LOCAL"'),
        (chat, "Riigi Teatajast reaalajas kontrollitud"),
        (evaluator, '"retains_user_text": False'),
    )
    if any(marker not in text for text, marker in required_markers):
        raise RuntimeError("V11.6 runtime/UI/pilot contract marker is missing.")
    print("ÕigusAI V11.6 live-pilot observability contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
