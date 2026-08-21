#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.legal_source_registry import LegalSourceRegistry, REGISTRY_VERSION
from services.rt_live_source import (
    RT_LIVE_ADAPTER_VERSION,
    RT_REGISTRY_SOURCE_CANDIDATES,
    RT_XML_API_BASE,
    verify_live_rt_source,
)

MANIFEST_PATH = PROJECT_ROOT / "data/rt_live_adapter_manifest.json"
_EXPECTED_KEYS = {
    "version",
    "source_registry_version",
    "registry_source_candidates",
    "official_xml_endpoint_template",
    "official_api_change_date",
    "live_adapter_available",
    "network_on_demand_only",
    "authority_classification_enabled",
    "current_revision_resolution_enabled",
    "writes_legal_corpus",
    "writes_case_law_corpus",
    "retrieval_integration_enabled",
    "model_context_integration_enabled",
}


def verify_contract() -> dict:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid RT live-adapter manifest: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != _EXPECTED_KEYS:
        raise RuntimeError("RT live-adapter manifest keys drifted from the V11.2.1 contract.")
    if manifest["version"] != RT_LIVE_ADAPTER_VERSION:
        raise RuntimeError("Unexpected RT live-adapter version.")
    if manifest["source_registry_version"] != REGISTRY_VERSION:
        raise RuntimeError("RT live adapter is pinned to a different source-registry version.")
    if tuple(manifest["registry_source_candidates"]) != RT_REGISTRY_SOURCE_CANDIDATES:
        raise RuntimeError("RT registry source candidates drifted from the audited contract.")
    if manifest["official_xml_endpoint_template"] != f"{RT_XML_API_BASE}/{{act_id}}/xml":
        raise RuntimeError("RT XML endpoint template drifted from the audited 2026 API shape.")
    if manifest["official_api_change_date"] != "2026-06-01":
        raise RuntimeError("RT API change date drifted from the audited contract.")
    if manifest["live_adapter_available"] is not True or manifest["network_on_demand_only"] is not True:
        raise RuntimeError("RT live adapter must be available only for explicit on-demand verification.")
    for flag in (
        "authority_classification_enabled",
        "current_revision_resolution_enabled",
        "writes_legal_corpus",
        "writes_case_law_corpus",
        "retrieval_integration_enabled",
        "model_context_integration_enabled",
    ):
        if manifest[flag] is not False:
            raise RuntimeError(f"V11.2.1 safety flag must remain false: {flag}")

    registry = LegalSourceRegistry.load(PROJECT_ROOT)
    for source_id in RT_REGISTRY_SOURCE_CANDIDATES:
        source = registry.source(source_id)
        if not source or not registry.supports_claim(source_id, "binding_rule"):
            raise RuntimeError(f"RT source family is missing from the audited binding registry: {source_id}")
        features = source.get("features") or {}
        if any(features.get(flag) is not False for flag in ("live_adapter_enabled", "retrieval_enabled", "model_context_enabled")):
            raise RuntimeError(f"V11.2 registry feature flags must remain disabled for {source_id}")

    return {
        "version": RT_LIVE_ADAPTER_VERSION,
        "registry_version": REGISTRY_VERSION,
        "source_candidates": list(RT_REGISTRY_SOURCE_CANDIDATES),
        "network_on_demand_only": True,
        "authority_classification_enabled": False,
        "current_revision_resolution_enabled": False,
        "retrieval_integration_enabled": False,
        "model_context_integration_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the V11.2.1 Riigi Teataja live-source adapter boundary.")
    parser.add_argument("--live-url", help="Optionally perform one explicit live XML verification against Riigi Teataja.")
    args = parser.parse_args()
    report = verify_contract()
    print("ÕigusAI V11.2.1 Riigi Teataja live-source adapter")
    for key, value in report.items():
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, list):
            value = ",".join(value)
        print(f"{key}: {value}")
    print("RT LIVE ADAPTER CONTRACT: PASS")
    if args.live_url:
        live = verify_live_rt_source(args.live_url)
        print("\nRiigi Teataja explicit live verification")
        for key in (
            "status",
            "act_id",
            "title",
            "canonical_url",
            "xml_url",
            "xml_sha256",
            "text_sha256",
            "authority_class",
            "currentness_verified",
        ):
            value = live[key]
            if isinstance(value, bool):
                value = str(value).lower()
            print(f"{key}: {value}")
        print("RT LIVE SOURCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
