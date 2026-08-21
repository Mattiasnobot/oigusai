#!/usr/bin/env python3
"""Deterministic V11.2 legal-source registry gate; no network access."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.legal_source_registry import LegalSourceRegistry, LegalSourceRegistryError


def main() -> int:
    try:
        registry = LegalSourceRegistry.load(PROJECT_ROOT)
        report = registry.snapshot()
    except LegalSourceRegistryError as exc:
        print(f"LEGAL SOURCE REGISTRY FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"LEGAL SOURCE REGISTRY ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print("ÕigusAI V11.2 official legal source registry")
    print(f"version: {report['version']}")
    print(f"source_count: {report['source_count']}")
    print(f"registry_sha256: {report['registry_sha256']}")
    print("binding_rule_sources: " + ",".join(report["binding_rule_sources"]))
    print(f"live_adapters_enabled: {str(report['live_adapters_enabled']).lower()}")
    print(f"retrieval_integration_enabled: {str(report['retrieval_integration_enabled']).lower()}")
    print(f"model_context_integration_enabled: {str(report['model_context_integration_enabled']).lower()}")
    print("LEGAL SOURCE REGISTRY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
