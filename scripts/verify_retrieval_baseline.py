#!/usr/bin/env python3
"""Fail closed when the audited V6.1 184/200 retrieval baseline becomes stale.

GitHub-hosted CI intentionally omits the local Ollama/LanceDB/reranker stack, so
it cannot honestly reproduce the GPU-backed V6.1 score.  Instead this gate pins
the audited score to the exact corpus, evaluation set and retrieval code blobs.
Any drift in those inputs requires a fresh local V6.1 audit and manifest update.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = PROJECT_ROOT / "eval" / "V61_CI_BASELINE.json"
EXPECTED_VERSION = "V6.1-ci-baseline-1"
EXPECTED_CASES = 200
EXPECTED_FLOOR = 184


class BaselineError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineError(f"Baseline file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"Invalid JSON in {path}: {exc}") from exc


def _git_blob_sha(root: Path, relative_path: str) -> str:
    target = root / relative_path
    if not target.is_file():
        raise BaselineError(f"Provenance file is missing: {relative_path}")
    result = subprocess.run(
        ["git", "hash-object", f"--path={relative_path}", "--", relative_path],
        cwd=str(root),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git hash-object failed").strip()
        raise BaselineError(f"Cannot hash {relative_path}: {detail}")
    value = result.stdout.strip().lower()
    if len(value) != 40:
        raise BaselineError(f"Unexpected git blob SHA for {relative_path}: {value}")
    return value


def verify_provenance(root: Path, rows: list[dict[str, Any]]) -> int:
    if not rows:
        raise BaselineError("Baseline provenance list is empty.")
    seen: set[str] = set()
    checked = 0
    for row in rows:
        if not isinstance(row, dict):
            raise BaselineError("Each provenance entry must be an object.")
        relative_path = str(row.get("path") or "").strip().replace("\\", "/")
        expected = str(row.get("git_blob_sha") or "").strip().lower()
        if not relative_path or len(expected) != 40:
            raise BaselineError("Provenance entry is missing path or git_blob_sha.")
        if relative_path in seen:
            raise BaselineError(f"Duplicate provenance path: {relative_path}")
        seen.add(relative_path)
        actual = _git_blob_sha(root, relative_path)
        if actual != expected:
            raise BaselineError(
                f"Audited V6.1 baseline is stale: {relative_path}\n"
                f"  expected blob: {expected}\n"
                f"  current blob:  {actual}\n"
                "Run the full local V6.1 retrieval evaluation, review the result, "
                "then update the baseline manifest deliberately."
            )
        checked += 1
    return checked


def validate_baseline(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise BaselineError("Baseline manifest must be a JSON object.")
    if str(manifest.get("version") or "") != EXPECTED_VERSION:
        raise BaselineError(f"Unsupported baseline version: {manifest.get('version')!r}")

    result = manifest.get("audited_result")
    if not isinstance(result, dict):
        raise BaselineError("audited_result must be an object.")
    cases = int(result.get("cases") or 0)
    passed = int(result.get("retrieval_passed") or 0)
    floor = int(result.get("retrieval_floor") or 0)
    failures = [str(value) for value in (result.get("failure_ids") or [])]
    if cases != EXPECTED_CASES:
        raise BaselineError(f"Audited case count must remain {EXPECTED_CASES}, got {cases}.")
    if floor != EXPECTED_FLOOR:
        raise BaselineError(f"Audited retrieval floor must remain {EXPECTED_FLOOR}, got {floor}.")
    if passed < floor:
        raise BaselineError(f"Audited result {passed}/{cases} is below the {floor} floor.")
    if len(set(failures)) != len(failures):
        raise BaselineError("failure_ids contains duplicates.")
    if passed + len(failures) != cases:
        raise BaselineError(
            "retrieval_passed + failure_ids must equal the audited case count."
        )

    query_cases = _load_json(root / "eval" / "query_cases.json")
    if not isinstance(query_cases, list) or len(query_cases) != cases:
        raise BaselineError(
            f"eval/query_cases.json must contain exactly {cases} cases for this baseline."
        )

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise BaselineError("runtime must be an object.")
    required_runtime = {
        "hybrid_retrieval_enabled": True,
        "embedding_model": "bge-m3",
        "reranker_enabled": True,
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "vector_rows": 22287,
    }
    for key, expected in required_runtime.items():
        if runtime.get(key) != expected:
            raise BaselineError(
                f"Audited runtime field {key} must be {expected!r}, got {runtime.get(key)!r}."
            )

    checked = verify_provenance(root, list(manifest.get("provenance") or []))
    return {
        "cases": cases,
        "retrieval_passed": passed,
        "retrieval_floor": floor,
        "failure_count": len(failures),
        "provenance_checked": checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the audited ÕigusAI V6.1 184/200 retrieval baseline provenance."
    )
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    args = parser.parse_args()
    path = Path(args.baseline)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        summary = validate_baseline(PROJECT_ROOT, _load_json(path))
    except BaselineError as exc:
        print(f"Retrieval baseline gate: FAIL\n{exc}", file=sys.stderr)
        return 2

    print("ÕigusAI audited V6.1 retrieval baseline")
    print(f"Score: {summary['retrieval_passed']}/{summary['cases']}")
    print(f"Required floor: {summary['retrieval_floor']}")
    print(f"Known failures: {summary['failure_count']}")
    print(f"Provenance files verified: {summary['provenance_checked']}")
    print("Baseline provenance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
