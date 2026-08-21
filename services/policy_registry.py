"""Auditable legal-policy metadata for ÕigusAI.

This registry stores only deterministic policy metadata. It never searches the
corpus, never creates legal authority and never decides whether a model answer
is legally correct. Callers still have to resolve every source ID against the
trusted legal corpus and verify the final answer separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class CoveragePolicyRule:
    """Audited source contract for one retrieval-obligation reason."""

    rule_id: str
    reason: str
    source_groups: Tuple[Tuple[str, ...], ...]
    rationale: str
    required_answer_terms: Tuple[Tuple[str, ...], ...] = ()


class CoveragePolicyRegistry:
    """Versioned, immutable registry consumed by CoverageVerifier."""

    VERSION = "V10.4-coverage-policy-1"

    _RULES: Mapping[str, CoveragePolicyRule] = MappingProxyType({
        "employment_context:redundancy_basis": CoveragePolicyRule(
            rule_id="coverage.employment.redundancy_basis.v1",
            reason="employment_context:redundancy_basis",
            source_groups=(("TLS_89",),),
            rationale="Koondamise aluse auditeeritud routing kasutab TLS § 89.",
        ),
        "employment_context:notice_period": CoveragePolicyRule(
            rule_id="coverage.employment.notice_period.v1",
            reason="employment_context:notice_period",
            source_groups=(("TLS_97",),),
            rationale="Koondamise etteteatamise auditeeritud routing kasutab TLS § 97.",
        ),
        "employment_context:termination_form": CoveragePolicyRule(
            rule_id="coverage.employment.termination_form.v1",
            reason="employment_context:termination_form",
            source_groups=(("TLS_95",),),
            rationale="Töölepingu ülesütlemise vorminõude auditeeritud routing kasutab TLS § 95.",
            required_answer_terms=(
                ("kirjalikku taasesitamist võimaldavas vormis",),
                ("tühine",),
            ),
        ),
        "fine_context:missed_deadline": CoveragePolicyRule(
            rule_id="coverage.fine.missed_deadline.v1",
            reason="fine_context:missed_deadline",
            source_groups=(("VTMS_118",),),
            rationale="Möödunud väärteokaebuse tähtaja auditeeritud routing kasutab VTMS § 118.",
        ),
        "fine_context:challenge_decision": CoveragePolicyRule(
            rule_id="coverage.fine.challenge_decision.v1",
            reason="fine_context:challenge_decision",
            source_groups=(("VTMS_114", "VTMS_118"),),
            rationale="Möödunud tähtaja kontekstis võib vaidlustamise katte anda VTMS § 114 või tähtaja ennistamist käsitlev VTMS § 118.",
            required_answer_terms=(("kaebus", "vaidlust", "maakoht"),),
        ),
        "fine_context:payment_plan": CoveragePolicyRule(
            rule_id="coverage.fine.payment_plan.v1",
            reason="fine_context:payment_plan",
            source_groups=(("KARS_66",),),
            rationale="Rahatrahvi ositi tasumise auditeeritud routing kasutab KarS § 66.",
        ),
    })

    @classmethod
    def get(cls, reason: str) -> Optional[CoveragePolicyRule]:
        return cls._RULES.get(str(reason or ""))

    @classmethod
    def snapshot(cls) -> Dict[str, object]:
        """Return a JSON-safe audit snapshot without exposing mutable internals."""
        rules = []
        for reason, rule in cls._RULES.items():
            rules.append({
                "rule_id": rule.rule_id,
                "reason": reason,
                "source_groups": [list(group) for group in rule.source_groups],
                "required_answer_terms": [
                    list(group) for group in rule.required_answer_terms
                ],
                "rationale": rule.rationale,
            })
        return {
            "version": cls.VERSION,
            "rule_count": len(rules),
            "rules": rules,
        }

    @classmethod
    def validate(cls) -> None:
        """Fail closed if a committed policy rule is malformed or ambiguous."""
        rule_ids = set()
        source_pattern = re.compile(r"^[A-ZÕÄÖÜ0-9]+_[A-ZÕÄÖÜ0-9]+$")
        for reason, rule in cls._RULES.items():
            if not reason or reason != rule.reason:
                raise RuntimeError(f"Coverage policy reason mismatch: {reason!r}")
            if not rule.rule_id or rule.rule_id in rule_ids:
                raise RuntimeError(f"Duplicate or empty coverage rule_id: {rule.rule_id!r}")
            rule_ids.add(rule.rule_id)
            if not rule.source_groups:
                raise RuntimeError(f"Coverage policy has no source groups: {rule.rule_id}")
            for group in rule.source_groups:
                if not group:
                    raise RuntimeError(f"Coverage policy has an empty source group: {rule.rule_id}")
                for source_id in group:
                    if not source_pattern.fullmatch(source_id):
                        raise RuntimeError(
                            f"Coverage policy has a non-canonical source ID: {source_id!r}"
                        )
            for term_group in rule.required_answer_terms:
                if not term_group or not all(str(term).strip() for term in term_group):
                    raise RuntimeError(
                        f"Coverage policy has an empty answer-term group: {rule.rule_id}"
                    )
            if not rule.rationale.strip():
                raise RuntimeError(f"Coverage policy rationale missing: {rule.rule_id}")


CoveragePolicyRegistry.validate()
