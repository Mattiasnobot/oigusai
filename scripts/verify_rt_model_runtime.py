#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.offline_ai import OfflineAIService
from services.verified_live_ai import (
    V11_5_MODEL_CONTEXT_VERSION,
    VerifiedLiveOfflineAIService,
)
from verifiers.source_verifier import SourceVerifier

RUNTIME_VERSION = "V11.5.1-verified-live-runtime-1"
MANIFEST_PATH = PROJECT_ROOT / "data" / "rt_model_runtime_manifest.json"
LAWS_PATH = PROJECT_ROOT / "data" / "laws.json"


def verify_contract() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = {
        "version": RUNTIME_VERSION,
        "depends_on_model_context_version": V11_5_MODEL_CONTEXT_VERSION,
        "application_runtime_wiring_enabled": True,
        "runtime_default_enabled": False,
        "runtime_env": "RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED",
        "main_uses_verified_live_offline_ai": True,
        "wrapper_subclasses_offline_ai": True,
        "same_analysis_laws_object_reused_downstream": True,
        "fail_closed_to_audited_local_context": True,
        "expected_failures_only_use_local_fallback": True,
        "unexpected_runtime_errors_propagate": True,
        "request_diagnostics_isolated": True,
        "runtime_outcome_counters_enabled": True,
        "unadmitted_live_model_context_enabled": False,
        "analysis_orchestrator_code_modified": False,
        "network_on_demand_only": True,
        "writes_legal_corpus": False,
        "persistent_live_cache_enabled": False,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(
                f"V11.5.1 manifest drift: {key}={payload.get(key)!r}, expected {value!r}"
            )

    if load_settings({}).rt_verified_live_model_context_enabled is not False:
        raise RuntimeError("V11.5.1 live model context must default to disabled.")
    if (
        load_settings({"RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED": "true"})
        .rt_verified_live_model_context_enabled
        is not True
    ):
        raise RuntimeError("V11.5.1 runtime opt-in environment switch is not wired.")
    if not issubclass(VerifiedLiveOfflineAIService, OfflineAIService):
        raise RuntimeError("V11.5.1 runtime wrapper must remain an OfflineAIService subclass.")

    service = VerifiedLiveOfflineAIService(
        settings=load_settings({}),
        live_model_context_enabled=False,
    )
    if service.live_model_context_stats() != {
        "admitted": 0,
        "local_fallback": 0,
        "unexpected_error": 0,
    }:
        raise RuntimeError("V11.5.1 runtime outcome counters are not initialized safely.")

    main_text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    expected_import = (
        "from services.verified_live_ai import "
        "VerifiedLiveOfflineAIService as OfflineAIService"
    )
    if expected_import not in main_text:
        raise RuntimeError("main.py is not wired to the V11.5.1 verified-live AI wrapper.")

    wrapper_source = inspect.getsource(VerifiedLiveOfflineAIService.analyze_case_structured)
    if 'laws[:] = admitted' not in wrapper_source:
        raise RuntimeError("V11.5.1 same-analysis_laws-object invariant drifted.")
    if "except (RTModelContextError, ValueError)" not in wrapper_source:
        raise RuntimeError("V11.5.1 expected-failure fallback boundary drifted.")
    return payload


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise RuntimeError("--as-of must be YYYY-MM-DD.") from exc
    if parsed > date.today():
        raise RuntimeError("Future-date live model-context verification is disabled.")
    return parsed


def load_local_candidate(source_id: str) -> dict:
    payload = json.loads(LAWS_PATH.read_text(encoding="utf-8"))
    laws = payload.get("laws", []) if isinstance(payload, dict) else payload
    wanted = str(source_id or "").strip().upper()
    for law in laws:
        if str(law.get("id", "")).strip().upper() != wanted:
            continue
        text = str(law.get("text") or "")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != law.get("content_hash"):
            raise RuntimeError(f"Local candidate {wanted} failed its corpus content hash.")
        return dict(law)
    raise RuntimeError(f"Local corpus does not contain {wanted}.")


def run_runtime_model_smoke(*, source_id: str, as_of: date, question: str) -> None:
    candidate = load_local_candidate(source_id)
    laws = [candidate]
    original_list_id = id(laws)
    ai = VerifiedLiveOfflineAIService(
        live_model_context_enabled=True,
        generation_seed=4242,
    )
    analysis, is_mock, claims = ai.analyze_case_structured(
        question,
        laws,
        as_of.isoformat(),
        [],
    )
    if is_mock:
        raise RuntimeError("V11.5.1 runtime model smoke used a mock response.")
    if id(laws) != original_list_id:
        raise RuntimeError("V11.5.1 runtime replaced the analysis_laws list object.")
    if not laws or laws[0].get("model_context_admission") != "VERIFIED_LIVE_BINDING_SECTION":
        raise RuntimeError(
            "V11.5.1 runtime did not admit a verified live section into the shared source list."
        )
    if laws[0].get("verification_status") != "BINDING_SECTION_VERIFIED":
        raise RuntimeError("V11.5.1 runtime model context lost BINDING_SECTION_VERIFIED status.")
    valid, sources = SourceVerifier().verify_sources(analysis, laws)
    if not valid or not sources:
        raise RuntimeError("V11.5.1 runtime model output failed the existing source verifier.")

    print()
    print("V11.5.1 normal-runtime verified-live model smoke")
    print(f"context_status: {ai.last_live_model_context.get('status')}")
    print(f"source_id: {laws[0]['id']}")
    print(f"act_id: {laws[0].get('act_id')}")
    print(f"section: {laws[0].get('section')}")
    print(f"model_context_admission: {laws[0].get('model_context_admission')}")
    print("same_analysis_laws_object: true")
    print("source_verifier: PASS")
    print(f"sources: {','.join(sources)}")
    print(f"structured_claim_count: {len(claims)}")
    print("RT VERIFIED LIVE RUNTIME MODEL SMOKE: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-model-smoke", action="store_true")
    parser.add_argument("--source-id", default="TLS_95")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument(
        "--question",
        default="Kas töölepingu võib üles öelda ainult suuliselt?",
    )
    args = parser.parse_args()

    manifest = verify_contract()
    print("ÕigusAI V11.5.1 verified-live runtime verifier")
    print(f"version: {manifest['version']}")
    print(
        "application_runtime_wiring_enabled: "
        f"{str(manifest['application_runtime_wiring_enabled']).lower()}"
    )
    print(f"runtime_default_enabled: {str(manifest['runtime_default_enabled']).lower()}")
    print(f"runtime_env: {manifest['runtime_env']}")
    print(
        "same_analysis_laws_object_reused_downstream: "
        f"{str(manifest['same_analysis_laws_object_reused_downstream']).lower()}"
    )
    print("RT VERIFIED LIVE RUNTIME CONTRACT: PASS")

    if args.runtime_model_smoke:
        run_runtime_model_smoke(
            source_id=args.source_id,
            as_of=parse_date(args.as_of),
            question=args.question,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
