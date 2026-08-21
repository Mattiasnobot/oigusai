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

from services.rt_current_retrieval import RT_CURRENT_RETRIEVAL_VERSION, VerifiedRTLiveRetrievalService

MANIFEST = PROJECT_ROOT / "data/rt_current_retrieval_manifest.json"


def verify_contract() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == RT_CURRENT_RETRIEVAL_VERSION
    assert manifest["current_revision_resolution_enabled"] is True
    assert manifest["verified_live_section_retrieval_enabled"] is True
    assert manifest["retrieval_composition_available"] is True
    assert manifest["runtime_default_enabled"] is False
    assert manifest["future_date_resolution_enabled"] is False
    assert manifest["exact_title_verification_required"] is True
    assert manifest["binding_authority_gate_required"] is True
    assert manifest["fallback_must_be_explicitly_labeled"] is True
    assert manifest["writes_legal_corpus"] is False
    assert manifest["writes_case_law_corpus"] is False
    assert manifest["model_context_integration_enabled"] is False
    assert manifest["network_on_demand_only"] is True
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-title")
    parser.add_argument("--section")
    parser.add_argument("--as-of")
    args = parser.parse_args()

    manifest = verify_contract()
    print("ÕigusAI V11.4 Riigi Teataja current-revision/live-retrieval verifier")
    print(f"version: {manifest['version']}")
    print(f"current_revision_resolution_enabled: {str(manifest['current_revision_resolution_enabled']).lower()}")
    print(f"verified_live_section_retrieval_enabled: {str(manifest['verified_live_section_retrieval_enabled']).lower()}")
    print(f"runtime_default_enabled: {str(manifest['runtime_default_enabled']).lower()}")
    print(f"model_context_integration_enabled: {str(manifest['model_context_integration_enabled']).lower()}")
    print("RT CURRENT RETRIEVAL CONTRACT: PASS")

    if args.live_title or args.section or args.as_of:
        if not (args.live_title and args.section):
            parser.error("--live-title and --section must be supplied together")
        check_date = date.fromisoformat(args.as_of) if args.as_of else date.today()
        result = VerifiedRTLiveRetrievalService().retrieve_sections(
            args.live_title,
            [args.section],
            as_of=check_date,
        )[0]
        print("\nRiigi Teataja verified live section")
        for key in (
            "verification_status", "act_id", "law_name", "section", "source_id",
            "authority_class", "as_of_date", "content_hash",
            "section_provenance_sha256", "revision_provenance_sha256", "xml_sha256",
        ):
            print(f"{key}: {result[key]}")
        print("RT VERIFIED LIVE SECTION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
