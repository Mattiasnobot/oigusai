from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.verified_live_ai import V11_5_MODEL_CONTEXT_VERSION

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "rt_verified_model_context_manifest.json"


def verify_contract() -> dict:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {
        "version": V11_5_MODEL_CONTEXT_VERSION,
        "depends_on_current_retrieval_version": "V11.4-rt-current-retrieval-1",
        "required_live_verification_status": "BINDING_SECTION_VERIFIED",
        "required_evidence_source": "rt_live_verified",
        "verified_live_model_context_integration_enabled": True,
        "runtime_default_enabled": False,
        "runtime_env": "RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED",
        "audited_local_context_remains_enabled": True,
        "local_fallback_may_masquerade_as_live": False,
        "future_date_assertions_enabled": False,
        "corpus_write_enabled": False,
        "persistent_live_cache_enabled": False,
        "downstream_source_verification_required": True,
        "downstream_evidence_verification_required": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"V11.5 manifest drift: {key}={payload.get(key)!r}, expected {value!r}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    manifest = verify_contract()
    print("ÕigusAI V11.5 verified live model-context verifier")
    print(f"version: {manifest['version']}")
    print(f"verified_live_model_context_integration_enabled: {str(manifest['verified_live_model_context_integration_enabled']).lower()}")
    print(f"runtime_default_enabled: {str(manifest['runtime_default_enabled']).lower()}")
    print(f"required_live_verification_status: {manifest['required_live_verification_status']}")
    print(f"audited_local_context_remains_enabled: {str(manifest['audited_local_context_remains_enabled']).lower()}")
    print(f"corpus_write_enabled: {str(manifest['corpus_write_enabled']).lower()}")
    print("RT VERIFIED MODEL CONTEXT CONTRACT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
