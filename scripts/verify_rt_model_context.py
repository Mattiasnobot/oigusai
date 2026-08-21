#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.offline_ai import OfflineAIService
from services.rt_current_retrieval import (
    RT_CURRENT_RETRIEVAL_VERSION,
    VerifiedRTLiveRetrievalService,
)
from services.rt_model_context import (
    RT_MODEL_CONTEXT_VERSION,
    admit_model_context,
)
from verifiers.source_verifier import SourceVerifier

MANIFEST_PATH = PROJECT_ROOT / "data" / "rt_model_context_manifest.json"


def verify_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    required_true = (
        "verified_live_model_context_enabled",
        "explicit_model_context_adapter_enabled",
        "application_runtime_wiring_enabled",
        "same_analysis_laws_object_reused_downstream",
        "live_content_hash_verified",
        "live_section_provenance_chain_verified",
        "live_exact_rt_urls_verified",
        "live_authority_currentness_required",
        "local_fallback_model_context_enabled",
        "local_fallback_requires_original_audited_candidate",
        "existing_model_output_source_verifier_reused",
        "existing_model_output_evidence_gate_reused",
        "network_on_demand_only",
    )
    if payload.get("version") != RT_MODEL_CONTEXT_VERSION:
        raise RuntimeError("V11.5 manifest version drifted.")
    if payload.get("depends_on_current_retrieval_version") != RT_CURRENT_RETRIEVAL_VERSION:
        raise RuntimeError("V11.5 dependency on V11.4 drifted.")
    for field in required_true:
        if payload.get(field) is not True:
            raise RuntimeError(f"V11.5 manifest must keep {field}=true.")
    required_false = (
        "runtime_default_enabled",
        "analysis_orchestrator_code_modified",
        "unverified_live_model_context_enabled",
        "writes_legal_corpus",
        "persistent_live_cache_enabled",
    )
    for field in required_false:
        if payload.get(field) is not False:
            raise RuntimeError(f"V11.5 manifest must keep {field}=false.")
    if payload.get("runtime_env") != "RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED":
        raise RuntimeError("V11.5 runtime opt-in environment variable drifted.")
    if payload.get("required_live_verification_status") != "BINDING_SECTION_VERIFIED":
        raise RuntimeError("V11.5 live admission status drifted.")
    if payload.get("required_live_evidence_source") != "rt_live_verified":
        raise RuntimeError("V11.5 live evidence-source gate drifted.")
    if payload.get("allowed_live_source_ids") != ["RT_NATIONAL_LAW", "RT_LOCAL_LAW"]:
        raise RuntimeError("V11.5 audited RT source mapping drifted.")
    return payload


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError("--as-of must be YYYY-MM-DD.") from exc
    if parsed > date.today():
        raise RuntimeError("Future-date model-context verification is disabled.")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-title")
    parser.add_argument("--section", action="append")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--domain", default="RT")
    parser.add_argument("--model-smoke", action="store_true")
    parser.add_argument(
        "--question",
        default="Mida ütleb kontrollitud säte selle olukorra kohta?",
    )
    args = parser.parse_args()

    manifest = verify_manifest()
    print("ÕigusAI V11.5 verified live model-context verifier")
    print(f"version: {manifest['version']}")
    print(
        "required_live_verification_status: "
        f"{manifest['required_live_verification_status']}"
    )
    print(f"application_runtime_wiring_enabled: {str(manifest['application_runtime_wiring_enabled']).lower()}")
    print(f"runtime_default_enabled: {str(manifest['runtime_default_enabled']).lower()}")
    print(f"runtime_env: {manifest['runtime_env']}")
    print(
        "same_analysis_laws_object_reused_downstream: "
        f"{str(manifest['same_analysis_laws_object_reused_downstream']).lower()}"
    )
    print("RT VERIFIED LIVE MODEL CONTEXT CONTRACT: PASS")

    if not args.live_title and not args.section and not args.model_smoke:
        return 0
    if not args.live_title or not args.section:
        raise RuntimeError("--live-title and at least one --section are required for live verification.")

    check_date = parse_date(args.as_of)
    live = VerifiedRTLiveRetrievalService().retrieve_sections(
        args.live_title,
        args.section,
        as_of=check_date,
        domain=args.domain,
    )
    admission = admit_model_context(live, expected_as_of=check_date)
    if not admission["laws"]:
        raise RuntimeError("V11.5 live verification produced no model-context laws.")

    print()
    print("Riigi Teataja model-context admission")
    print(f"status: {admission['status']}")
    print(f"live_count: {admission['live_count']}")
    print(f"local_count: {admission['local_count']}")
    for law in admission["laws"]:
        print(
            f"{law['id']}: {law['verification_status']} -> "
            f"{law['model_context_admission']}"
        )
        print(f"act_id: {law['act_id']}")
        print(f"section: {law['section']}")
        print(f"section_provenance_sha256: {law['section_provenance_sha256']}")

    if not args.model_smoke:
        print("RT VERIFIED LIVE MODEL CONTEXT: PASS")
        return 0

    ai = OfflineAIService(generation_seed=4242)
    analysis, is_mock, claims = ai.analyze_case_structured(
        args.question,
        admission["laws"],
        check_date.isoformat(),
    )
    if is_mock:
        raise RuntimeError("Model smoke used a mock response.")
    valid, source_ids = SourceVerifier().verify_sources(analysis, admission["laws"])
    if not valid:
        raise RuntimeError("Model smoke response failed the existing source verifier.")
    if not source_ids:
        raise RuntimeError("Model smoke returned no verified source IDs.")

    print()
    print("Ollama verified-live model smoke")
    print("source_verifier: PASS")
    print(f"sources: {','.join(source_ids)}")
    print(f"structured_claim_count: {len(claims)}")
    print("RT VERIFIED LIVE MODEL SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
