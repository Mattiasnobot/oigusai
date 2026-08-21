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

from services.rt_authority import RT_AUTHORITY_VERSION, verify_live_rt_binding_authority

MANIFEST = PROJECT_ROOT / "data/rt_authority_manifest.json"
LIVE_MANIFEST = PROJECT_ROOT / "data/rt_live_adapter_manifest.json"
EXPECTED_MAPPING = {"RT I": "RT_NATIONAL_LAW", "RT IV": "RT_LOCAL_LAW"}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return value


def verify_contract() -> dict:
    manifest = _load(MANIFEST)
    previous = _load(LIVE_MANIFEST)
    if manifest.get("version") != RT_AUTHORITY_VERSION:
        raise RuntimeError("Unexpected V11.3 RT authority manifest version")
    if manifest.get("depends_on_live_adapter_version") != previous.get("version"):
        raise RuntimeError("V11.3 does not pin the committed V11.2.1 live adapter")
    if manifest.get("source_registry_version") != "V11.2-official-source-registry-1":
        raise RuntimeError("Unexpected source registry version")
    if manifest.get("audited_source_mapping") != EXPECTED_MAPPING:
        raise RuntimeError("RT publication-series mapping drifted")
    if manifest.get("audited_binding_act_types") != ["seadus", "määrus"]:
        raise RuntimeError("RT binding act-type policy drifted")
    for key in (
        "authority_classification_enabled",
        "currentness_verification_enabled",
        "binding_claim_policy_gate_enabled",
        "network_on_demand_only",
    ):
        if manifest.get(key) is not True:
            raise RuntimeError(f"V11.3 capability must be enabled: {key}")
    for key in (
        "current_revision_resolution_enabled",
        "future_date_assertions_enabled",
        "writes_legal_corpus",
        "writes_case_law_corpus",
        "retrieval_integration_enabled",
        "model_context_integration_enabled",
    ):
        if manifest.get(key) is not False:
            raise RuntimeError(f"V11.3 safety boundary must remain disabled: {key}")
    if previous.get("authority_classification_enabled") is not False:
        raise RuntimeError("V11.2.1 historical live-adapter contract was rewritten")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ÕigusAI V11.3 RT authority/currentness contract")
    parser.add_argument("--live-url", help="Explicit RT act URL/id to verify live after the offline contract gate")
    parser.add_argument("--as-of", help="Legal date YYYY-MM-DD; defaults to today")
    args = parser.parse_args()

    manifest = verify_contract()
    print("ÕigusAI V11.3 Riigi Teataja authority/currentness verifier")
    print(f"version: {manifest['version']}")
    print("source_mapping: RT I->RT_NATIONAL_LAW,RT IV->RT_LOCAL_LAW")
    print("authority_classification_enabled: true")
    print("currentness_verification_enabled: true")
    print("current_revision_resolution_enabled: false")
    print("future_date_assertions_enabled: false")
    print("retrieval_integration_enabled: false")
    print("model_context_integration_enabled: false")
    print("RT AUTHORITY/CURRENTNESS CONTRACT: PASS")

    if not args.live_url:
        return 0
    check_date = date.fromisoformat(args.as_of) if args.as_of else date.today()
    result = verify_live_rt_binding_authority(args.live_url, as_of=check_date)
    print("\nRiigi Teataja binding-source live verification")
    for key in (
        "status",
        "act_id",
        "title",
        "source_id",
        "authority_class",
        "as_of_date",
        "issuer",
        "act_type",
        "text_type",
        "publication_marker",
        "valid_from",
        "valid_to_exclusive",
        "revision_provenance_sha256",
        "xml_sha256",
        "text_sha256",
    ):
        print(f"{key}: {result.get(key)}")
    print("RT BINDING SOURCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
