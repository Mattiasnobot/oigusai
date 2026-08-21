"""V11.2 fail-closed registry for official and institutional legal-information sources.

The registry classifies source authority; it does not fetch any source and does
not enable retrieval/model context. A source may support a claim class only when
its audited authority class is explicitly allowed by the registry policy.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import urlsplit

REGISTRY_VERSION = "V11.2-official-source-registry-1"
MANIFEST_VERSION = "V11.2-source-registry-manifest-1"
_SOURCE_ID = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,80}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_REQUIRED_SOURCE_IDS = frozenset({
    "RT_NATIONAL_LAW",
    "RT_LOCAL_LAW",
    "RT_CASE_LAW",
    "OFFICIAL_NOTICES",
    "RIIGIKOGU_PROCEEDINGS",
    "EIS_DRAFTS",
    "RIIGIKOHUS_DECISIONS",
    "EURLEX_EU_LAW",
    "CURIA_CASE_LAW",
    "HUDOC_ECHR_CASE_LAW",
    "TTJA_GUIDANCE",
    "AKI_GUIDANCE",
    "TI_GUIDANCE",
    "EMTA_GUIDANCE",
    "FINANTSINSPEKTSIOON_GUIDANCE",
})
_EXPECTED_POLICY: Mapping[str, frozenset[str]] = {
    "binding_rule": frozenset({
        "binding_national_law",
        "binding_local_law",
        "binding_eu_law",
    }),
    "court_holding": frozenset({"judicial_decision"}),
    "legislative_history": frozenset({"legislative_history"}),
    "draft_legislation": frozenset({"draft_legislation"}),
    "official_notice": frozenset({"official_notice"}),
    "institutional_interpretation": frozenset({"institutional_interpretation"}),
    "regulator_guidance": frozenset({"regulator_guidance"}),
    "secondary_analysis": frozenset({"secondary_analysis"}),
}
_FEATURE_FLAGS = (
    "live_adapter_enabled",
    "retrieval_enabled",
    "model_context_enabled",
)


class LegalSourceRegistryError(RuntimeError):
    pass


def compute_registry_sha256(registry: Mapping[str, Any]) -> str:
    raw = json.dumps(
        registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path, expected_type: type) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LegalSourceRegistryError(f"Required source-registry file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LegalSourceRegistryError(f"Invalid source-registry JSON {path}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise LegalSourceRegistryError(
            f"{path} must contain {expected_type.__name__}, got {type(value).__name__}."
        )
    return value


class LegalSourceRegistry:
    """Immutable-by-convention validated registry snapshot."""

    def __init__(self, registry: Mapping[str, Any], manifest: Mapping[str, Any]):
        self.registry = dict(registry)
        self.manifest = dict(manifest)
        self._sources: Dict[str, Dict[str, Any]] = {}
        self.validate()

    @classmethod
    def load(cls, project_root: Path) -> "LegalSourceRegistry":
        registry = _load_json(project_root / "data/legal_source_registry.json", dict)
        manifest = _load_json(project_root / "data/legal_source_registry_manifest.json", dict)
        return cls(registry, manifest)

    def validate(self) -> None:
        if self.registry.get("version") != REGISTRY_VERSION:
            raise LegalSourceRegistryError("Unexpected legal source registry version.")
        if self.manifest.get("version") != MANIFEST_VERSION:
            raise LegalSourceRegistryError("Unexpected legal source registry manifest version.")
        if self.manifest.get("registry_path") != "data/legal_source_registry.json":
            raise LegalSourceRegistryError("Legal source registry path is not canonical.")

        policy = self.registry.get("policy")
        if not isinstance(policy, dict):
            raise LegalSourceRegistryError("Legal source registry policy must be an object.")
        normalized_policy: Dict[str, frozenset[str]] = {}
        for claim_class, values in policy.items():
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise LegalSourceRegistryError(f"Invalid authority policy for {claim_class!r}.")
            normalized_policy[str(claim_class)] = frozenset(values)
        if normalized_policy != _EXPECTED_POLICY:
            raise LegalSourceRegistryError("Legal source authority policy drifted from the audited V11.2 contract.")

        sources = self.registry.get("sources")
        if not isinstance(sources, list) or len(sources) < 15:
            raise LegalSourceRegistryError("Legal source registry must contain at least 15 audited sources.")

        verified: Dict[str, Dict[str, Any]] = {}
        for raw in sources:
            source = self._validate_source(raw)
            source_id = source["id"]
            if source_id in verified:
                raise LegalSourceRegistryError(f"Duplicate legal source id: {source_id}")
            verified[source_id] = source

        missing = sorted(_REQUIRED_SOURCE_IDS.difference(verified))
        if missing:
            raise LegalSourceRegistryError(
                "Required legal source families are missing: " + ", ".join(missing)
            )

        count = self.manifest.get("source_count")
        if not isinstance(count, int) or count != len(verified):
            raise LegalSourceRegistryError(
                f"Legal source count mismatch: manifest={count!r}, registry={len(verified)}."
            )
        expected_hash = str(self.manifest.get("registry_sha256") or "").strip().lower()
        current_hash = compute_registry_sha256(self.registry)
        if expected_hash != current_hash:
            raise LegalSourceRegistryError("Legal source registry SHA-256 does not match its manifest.")

        for flag in (
            "live_adapters_enabled",
            "retrieval_integration_enabled",
            "model_context_integration_enabled",
        ):
            if self.manifest.get(flag) is not False:
                raise LegalSourceRegistryError(f"V11.2 integration flag must remain false: {flag}")

        self._sources = verified

    def _validate_source(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise LegalSourceRegistryError("Every legal source entry must be an object.")
        source_id = str(raw.get("id") or "").strip().upper()
        if not _SOURCE_ID.fullmatch(source_id):
            raise LegalSourceRegistryError(f"Invalid legal source id: {source_id!r}")

        text_fields = (
            "display_name",
            "source_group",
            "authority_class",
            "source_kind",
            "jurisdiction",
        )
        clean: Dict[str, Any] = {"id": source_id}
        for field in text_fields:
            value = str(raw.get(field) or "").strip()
            if not value:
                raise LegalSourceRegistryError(f"{source_id} is missing {field}.")
            clean[field] = value

        authority = clean["authority_class"]
        if authority not in {item for values in _EXPECTED_POLICY.values() for item in values}:
            raise LegalSourceRegistryError(f"{source_id} has unknown authority class: {authority}")

        hosts = raw.get("canonical_hosts")
        if not isinstance(hosts, list) or not hosts:
            raise LegalSourceRegistryError(f"{source_id} has no canonical hosts.")
        clean_hosts = []
        for host in hosts:
            value = str(host or "").strip().lower()
            if not value or not _HOST.fullmatch(value) or value.startswith(".") or ".." in value:
                raise LegalSourceRegistryError(f"{source_id} has invalid canonical host: {host!r}")
            if value in clean_hosts:
                raise LegalSourceRegistryError(f"{source_id} has duplicate canonical host: {value}")
            clean_hosts.append(value)
        clean["canonical_hosts"] = tuple(clean_hosts)

        claims = raw.get("allowed_claim_classes")
        if not isinstance(claims, list) or not claims:
            raise LegalSourceRegistryError(f"{source_id} has no allowed claim classes.")
        clean_claims = []
        for claim_class in claims:
            value = str(claim_class or "").strip()
            allowed_authorities = _EXPECTED_POLICY.get(value)
            if not allowed_authorities:
                raise LegalSourceRegistryError(f"{source_id} has unknown claim class: {value}")
            if authority not in allowed_authorities:
                raise LegalSourceRegistryError(
                    f"{source_id} authority {authority!r} cannot support claim class {value!r}."
                )
            if value in clean_claims:
                raise LegalSourceRegistryError(f"{source_id} has duplicate claim class: {value}")
            clean_claims.append(value)
        clean["allowed_claim_classes"] = tuple(clean_claims)

        features = raw.get("features")
        if not isinstance(features, dict):
            raise LegalSourceRegistryError(f"{source_id} features must be an object.")
        if set(features) != set(_FEATURE_FLAGS):
            raise LegalSourceRegistryError(f"{source_id} feature keys do not match the V11.2 contract.")
        for flag in _FEATURE_FLAGS:
            if features.get(flag) is not False:
                raise LegalSourceRegistryError(f"{source_id} must keep {flag}=false in V11.2.")
        clean["features"] = {flag: False for flag in _FEATURE_FLAGS}
        return clean

    def source(self, source_id: str) -> Dict[str, Any]:
        return dict(self._sources.get(str(source_id or "").strip().upper(), {}))

    def supports_claim(self, source_id: str, claim_class: str) -> bool:
        source = self._sources.get(str(source_id or "").strip().upper())
        return bool(source and str(claim_class or "").strip() in source["allowed_claim_classes"])

    def validates_url(self, source_id: str, url: str) -> bool:
        source = self._sources.get(str(source_id or "").strip().upper())
        if not source:
            return False
        try:
            parsed = urlsplit(str(url or "").strip())
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname.lower() in source["canonical_hosts"]
            and parsed.username is None
            and parsed.password is None
        )

    def snapshot(self) -> Dict[str, Any]:
        authority_counts: Dict[str, int] = {}
        for source in self._sources.values():
            authority = source["authority_class"]
            authority_counts[authority] = authority_counts.get(authority, 0) + 1
        return {
            "version": REGISTRY_VERSION,
            "source_count": len(self._sources),
            "registry_sha256": compute_registry_sha256(self.registry),
            "binding_rule_sources": sorted(
                source_id for source_id in self._sources
                if self.supports_claim(source_id, "binding_rule")
            ),
            "authority_counts": dict(sorted(authority_counts.items())),
            "live_adapters_enabled": False,
            "retrieval_integration_enabled": False,
            "model_context_integration_enabled": False,
        }
