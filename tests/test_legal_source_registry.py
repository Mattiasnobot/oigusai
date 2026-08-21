import copy
import json
import unittest
from pathlib import Path

from services.legal_source_registry import (
    LegalSourceRegistry,
    LegalSourceRegistryError,
    compute_registry_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LegalSourceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_data = json.loads(
            (PROJECT_ROOT / "data/legal_source_registry.json").read_text(encoding="utf-8")
        )
        cls.manifest_data = json.loads(
            (PROJECT_ROOT / "data/legal_source_registry_manifest.json").read_text(encoding="utf-8")
        )

    def build(self, registry=None, manifest=None):
        registry = copy.deepcopy(registry if registry is not None else self.registry_data)
        manifest = copy.deepcopy(manifest if manifest is not None else self.manifest_data)
        manifest["source_count"] = len(registry.get("sources", []))
        manifest["registry_sha256"] = compute_registry_sha256(registry)
        return LegalSourceRegistry(registry, manifest)

    def test_committed_registry_is_disabled_and_contains_broad_source_set(self):
        registry = self.build()
        snapshot = registry.snapshot()
        self.assertGreaterEqual(snapshot["source_count"], 20)
        self.assertFalse(snapshot["live_adapters_enabled"])
        self.assertFalse(snapshot["retrieval_integration_enabled"])
        self.assertFalse(snapshot["model_context_integration_enabled"])
        self.assertTrue(registry.source("TTJA_GUIDANCE"))
        self.assertTrue(registry.source("AKI_GUIDANCE"))
        self.assertTrue(registry.source("TI_GUIDANCE"))

    def test_only_primary_binding_sources_can_support_binding_rule(self):
        registry = self.build()
        self.assertTrue(registry.supports_claim("RT_NATIONAL_LAW", "binding_rule"))
        self.assertTrue(registry.supports_claim("RT_LOCAL_LAW", "binding_rule"))
        self.assertTrue(registry.supports_claim("EURLEX_EU_LAW", "binding_rule"))
        self.assertFalse(registry.supports_claim("TTJA_GUIDANCE", "binding_rule"))
        self.assertFalse(registry.supports_claim("RIIGIKOGU_PROCEEDINGS", "binding_rule"))
        self.assertFalse(registry.supports_claim("RT_CASE_LAW", "binding_rule"))

    def test_regulator_guidance_cannot_be_tampered_into_binding_law(self):
        data = copy.deepcopy(self.registry_data)
        ttja = next(item for item in data["sources"] if item["id"] == "TTJA_GUIDANCE")
        ttja["allowed_claim_classes"].append("binding_rule")
        with self.assertRaises(LegalSourceRegistryError):
            self.build(data)

    def test_draft_source_cannot_be_reclassified_as_binding_authority(self):
        data = copy.deepcopy(self.registry_data)
        eis = next(item for item in data["sources"] if item["id"] == "EIS_DRAFTS")
        eis["authority_class"] = "binding_national_law"
        with self.assertRaises(LegalSourceRegistryError):
            self.build(data)

    def test_exact_https_host_is_required(self):
        registry = self.build()
        self.assertTrue(registry.validates_url("TTJA_GUIDANCE", "https://www.ttja.ee/eraklient"))
        self.assertFalse(registry.validates_url("TTJA_GUIDANCE", "http://www.ttja.ee/eraklient"))
        self.assertFalse(registry.validates_url("TTJA_GUIDANCE", "https://ttja.ee.evil.example/"))
        self.assertFalse(registry.validates_url("TTJA_GUIDANCE", "https://evil.example/?next=ttja.ee"))

    def test_every_source_feature_remains_disabled(self):
        data = copy.deepcopy(self.registry_data)
        source = data["sources"][0]
        source["features"]["live_adapter_enabled"] = True
        with self.assertRaises(LegalSourceRegistryError):
            self.build(data)

    def test_required_source_family_removal_fails_closed(self):
        data = copy.deepcopy(self.registry_data)
        data["sources"] = [item for item in data["sources"] if item["id"] != "TTJA_GUIDANCE"]
        with self.assertRaises(LegalSourceRegistryError):
            self.build(data)

    def test_registry_manifest_hash_detects_content_drift(self):
        data = copy.deepcopy(self.registry_data)
        data["sources"][0]["display_name"] += " changed"
        with self.assertRaises(LegalSourceRegistryError):
            LegalSourceRegistry(data, copy.deepcopy(self.manifest_data))


if __name__ == "__main__":
    unittest.main()
