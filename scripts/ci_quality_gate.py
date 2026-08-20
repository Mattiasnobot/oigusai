#!/usr/bin/env python3
"""Deterministic CPU-only quality gate for GitHub CI.

This intentionally exercises the same trusted corpus and query-understanding
startup path as runtime while disabling optional local ML/vector retrieval.
It never calls Ollama or the live Riigi Teataja service.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CONFIG_SCHEMA_VERSION, load_settings
from services.legal_search import LegalSearchService
from services.vector_search import compute_corpus_fingerprint


class QualityGateError(RuntimeError):
    pass


def _load_json(path: Path, *, expected_type: type) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QualityGateError(f"Required file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise QualityGateError(
            f"{path} must contain {expected_type.__name__}, got {type(value).__name__}."
        )
    return value


def validate_laws(laws: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(laws)
    if not records:
        raise QualityGateError("Trusted legal corpus is empty.")

    ids = []
    required = ("id", "title", "text", "source")
    for index, law in enumerate(records, start=1):
        if not isinstance(law, dict):
            raise QualityGateError(f"Corpus row {index} is not an object.")
        missing = [name for name in required if not str(law.get(name) or "").strip()]
        if missing:
            raise QualityGateError(
                f"Corpus row {index} is missing required values: {', '.join(missing)}"
            )
        ids.append(str(law["id"]).upper())

    if len(set(ids)) != len(ids):
        duplicates = sorted({law_id for law_id in ids if ids.count(law_id) > 1})
        raise QualityGateError(
            "Duplicate trusted-corpus IDs: " + ", ".join(duplicates[:20])
        )

    fingerprint = compute_corpus_fingerprint(records)
    if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
        raise QualityGateError("Corpus fingerprint is not a valid SHA-256 digest.")

    return {
        "legal_sections": len(records),
        "unique_ids": len(ids),
        "corpus_fingerprint": fingerprint,
    }


def validate_evaluation_assets(project_root: Path) -> Dict[str, Any]:
    cases = _load_json(project_root / "eval/query_cases.json", expected_type=list)
    baseline = _load_json(
        project_root / "eval/V91_WORKFLOW_BASELINE_2026-08-11.json",
        expected_type=dict,
    )
    case_ids = [str(item.get("id") or "").strip() for item in cases if isinstance(item, dict)]
    if len(case_ids) != len(cases) or any(not value for value in case_ids):
        raise QualityGateError("Every workflow evaluation case must have a non-empty id.")
    if len(set(case_ids)) != len(case_ids):
        raise QualityGateError("Workflow evaluation case IDs must be unique.")

    expected_cases = int(baseline.get("cases") or 0)
    if expected_cases <= 0 or len(cases) != expected_cases:
        raise QualityGateError(
            f"Workflow case count changed: baseline={expected_cases}, current={len(cases)}."
        )
    if baseline.get("acceptance_passed") is not True:
        raise QualityGateError("Committed V9.1 workflow baseline is not an accepted baseline.")
    retrieval_floor = int(baseline.get("retrieval_baseline_required") or 0)
    if retrieval_floor <= 0 or retrieval_floor > expected_cases:
        raise QualityGateError("Workflow retrieval baseline is invalid.")

    return {
        "workflow_cases": len(cases),
        "retrieval_floor": retrieval_floor,
    }


def deterministic_settings() -> Any:
    env = dict(os.environ)
    env.update(
        {
            "HYBRID_RETRIEVAL_ENABLED": "false",
            "RERANKER_ENABLED": "false",
            "ALLOW_LIVE_RT_FALLBACK": "false",
            "ALLOW_MOCK_ANALYSIS": "false",
            "APP_RELOAD": "false",
            "LOG_LEVEL": "WARNING",
        }
    )
    return load_settings(env)


def run_gate(project_root: Path = PROJECT_ROOT) -> Dict[str, Any]:
    settings = deterministic_settings()
    service = LegalSearchService(
        data_file=settings.legal_data_file,
        settings=settings,
        use_riigi_teataja=False,
    )
    corpus = validate_laws(service.laws)
    evaluation = validate_evaluation_assets(project_root)
    if CONFIG_SCHEMA_VERSION <= 0:
        raise QualityGateError("CONFIG_SCHEMA_VERSION must be positive.")
    return {
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "query_vocabulary_terms": service.query_understanding.vocabulary_size,
        **corpus,
        **evaluation,
    }


def main() -> int:
    try:
        report = run_gate()
    except QualityGateError as exc:
        print(f"QUALITY GATE FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"QUALITY GATE ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print("ÕigusAI deterministic CI quality gate")
    for key, value in report.items():
        print(f"{key}: {value}")
    print("QUALITY GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
