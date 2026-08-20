"""Deterministic obligation coverage verification for ÕigusAI V10.3.

Coverage is deliberately narrower than legal reasoning.  The verifier checks
whether an audited retrieval obligation has at least one trusted source in the
model context and whether the final cited answer actually uses the required
source group.  It never creates a new authority and it never treats model
memory as a legal source.

The small rule map below is intentionally explicit and temporary.  V10.4 moves
these auditable obligation -> source-group rules into the policy registry.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class CoverageRule:
    """Audited source groups required for one retrieval-obligation reason."""

    source_groups: Tuple[Tuple[str, ...], ...]
    rationale: str


class CoverageVerifier:
    """Verify retrieval and answer coverage without making legal conclusions."""

    STATUS_COVERED = "COVERED"
    STATUS_ANSWER_MISSING = "ANSWER_MISSING"
    STATUS_SOURCE_MISSING = "SOURCE_MISSING"
    STATUS_RULE_UNRESOLVED = "RULE_UNRESOLVED"

    _RULES: Mapping[str, CoverageRule] = {
        "employment_context:redundancy_basis": CoverageRule(
            source_groups=(("TLS_89",),),
            rationale="Koondamise aluse auditeeritud routing kasutab TLS § 89.",
        ),
        "employment_context:notice_period": CoverageRule(
            source_groups=(("TLS_97",),),
            rationale="Koondamise etteteatamise auditeeritud routing kasutab TLS § 97.",
        ),
        "employment_context:termination_form": CoverageRule(
            source_groups=(("TLS_95",),),
            rationale="Töölepingu ülesütlemise vorminõude auditeeritud routing kasutab TLS § 95.",
        ),
        "fine_context:missed_deadline": CoverageRule(
            source_groups=(("VTMS_118",),),
            rationale="Möödunud väärteokaebuse tähtaja auditeeritud routing kasutab VTMS § 118.",
        ),
        "fine_context:challenge_decision": CoverageRule(
            source_groups=(("VTMS_114", "VTMS_118"),),
            rationale="Möödunud tähtaja kontekstis võib vaidlustamise katte anda VTMS § 114 või tähtaja ennistamist käsitlev VTMS § 118.",
        ),
        "fine_context:payment_plan": CoverageRule(
            source_groups=(("KARS_66",),),
            rationale="Rahatrahvi ositi tasumise auditeeritud routing kasutab KarS § 66.",
        ),
    }

    @staticmethod
    def _normalize_id(value: Any) -> str:
        return re.sub(r"[^A-ZÕÄÖÜ0-9_]", "", str(value or "").strip().upper())

    @classmethod
    def verify(
        cls,
        obligation_plan: Any,
        available_laws: Iterable[Dict[str, Any]],
        verified_sources: Iterable[str],
    ) -> Dict[str, Any]:
        """Return an inspectable coverage report for the current answer."""
        obligations = list(getattr(obligation_plan, "obligations", ()) or ())
        available_ids = {
            cls._normalize_id(law.get("id"))
            for law in (available_laws or [])
            if cls._normalize_id(law.get("id"))
        }
        cited_ids = {
            cls._normalize_id(value)
            for value in (verified_sources or [])
            if cls._normalize_id(value)
        }

        rows: List[Dict[str, Any]] = []
        missing_answer: List[str] = []
        missing_source: List[str] = []
        unresolved_rules: List[str] = []
        enforced_count = 0
        covered_count = 0

        for obligation in obligations:
            kind = str(getattr(obligation, "kind", "") or "")
            reason = str(getattr(obligation, "reason", "") or "")
            answer_requirement = str(
                getattr(obligation, "answer_requirement", "") or ""
            )
            rule = cls._RULES.get(reason)
            if rule is None:
                unresolved_rules.append(kind)
                rows.append({
                    "kind": kind,
                    "reason": reason,
                    "answer_requirement": answer_requirement,
                    "enforced": False,
                    "status": cls.STATUS_RULE_UNRESOLVED,
                    "expected_source_groups": [],
                    "candidate_sources": [],
                    "cited_sources": [],
                    "rationale": "V10.3-l puudub sellele kohustusele veel auditeeritud coverage-reegel.",
                })
                continue

            enforced_count += 1
            normalized_groups = tuple(
                tuple(cls._normalize_id(value) for value in group if cls._normalize_id(value))
                for group in rule.source_groups
            )
            source_missing_groups = [
                group for group in normalized_groups
                if not available_ids.intersection(group)
            ]
            answer_missing_groups = [
                group for group in normalized_groups
                if available_ids.intersection(group)
                and not cited_ids.intersection(group)
            ]
            candidate_sources = sorted({
                source_id
                for group in normalized_groups
                for source_id in group
                if source_id in available_ids
            })
            cited_sources = sorted(set(candidate_sources).intersection(cited_ids))

            if source_missing_groups:
                status = cls.STATUS_SOURCE_MISSING
                missing_source.append(kind)
            elif answer_missing_groups:
                status = cls.STATUS_ANSWER_MISSING
                missing_answer.append(kind)
            else:
                status = cls.STATUS_COVERED
                covered_count += 1

            rows.append({
                "kind": kind,
                "reason": reason,
                "answer_requirement": answer_requirement,
                "enforced": True,
                "status": status,
                "expected_source_groups": [list(group) for group in normalized_groups],
                "candidate_sources": candidate_sources,
                "cited_sources": cited_sources,
                "rationale": rule.rationale,
            })

        passed = not missing_answer and not missing_source
        return {
            "passed": passed,
            "enforced": enforced_count > 0,
            "enforced_count": enforced_count,
            "covered_count": covered_count,
            "missing_answer": missing_answer,
            "missing_source": missing_source,
            "unresolved_rules": unresolved_rules,
            "needs_repair": bool(missing_answer) and not bool(missing_source),
            "obligations": rows,
        }

    @classmethod
    def repair_instructions(cls, report: Mapping[str, Any]) -> str:
        """Build deterministic instructions for one model coverage-repair attempt."""
        rows = [
            row for row in (report.get("obligations") or [])
            if isinstance(row, dict) and row.get("enforced")
        ]
        if not rows or not report.get("needs_repair"):
            return ""

        targets: List[Tuple[str, str, str]] = []
        for row in rows:
            candidate_sources = [
                cls._normalize_id(value)
                for value in (row.get("candidate_sources") or [])
                if cls._normalize_id(value)
            ]
            if not candidate_sources:
                continue
            targets.append((
                str(row.get("kind") or ""),
                str(row.get("answer_requirement") or row.get("kind") or ""),
                candidate_sources[0],
            ))

        if not targets:
            return ""

        lines = [
            "KATVUSE PARANDUS — RANGE CLAIM-CHECKLIST:",
            "Eelmine vastus ei katnud kõiki auditeeritud vastusekohustusi.",
            f"Tagasta claims massiivis TÄPSELT {len(targets)} elementi — üks iga alloleva kohustuse kohta ja samas järjekorras.",
            "Iga claim peab kasutama just talle määratud source_id väärtust ning evidence peab olema sellest samast allikast täpselt kopeeritud katkematu katkend.",
            "Ära kuluta ühtegi claims elementi kõrvalteemale, soovitusele ega muule source_id-le.",
        ]
        for index, (kind, requirement, source_id) in enumerate(targets, start=1):
            lines.extend([
                f"KOHUSTUS {index}/{len(targets)} [{kind}]",
                f"- küsimus: {requirement}",
                f"- source_id: {source_id}",
            ])
        lines.extend([
            "Kui allikatekst ei võimalda laiemat järeldust, tee väide evidence teksti kitsaks ümberütluseks.",
            "Ära lisa ühtegi normi, tähtaega, arvu ega järeldust, mida määratud allikatekst ei toeta.",
        ])
        return "\n".join(lines)

    @classmethod
    def build_source_digest(
        cls,
        report: Mapping[str, Any],
        laws: Sequence[Dict[str, Any]],
    ) -> str:
        """Build a coverage-preserving deterministic digest from exact source text.

        The digest contains source excerpts, not newly inferred legal conclusions.
        It is used only after a model answer cannot satisfy the audited coverage
        requirements.
        """
        if not report.get("enforced"):
            return ""
        if report.get("missing_source"):
            return ""

        law_map = {
            cls._normalize_id(law.get("id")): law
            for law in (laws or [])
            if cls._normalize_id(law.get("id"))
        }
        ordered_ids: List[str] = []
        for row in report.get("obligations") or []:
            if not isinstance(row, dict) or not row.get("enforced"):
                continue
            for group in row.get("expected_source_groups") or []:
                chosen = next(
                    (
                        cls._normalize_id(source_id)
                        for source_id in group
                        if cls._normalize_id(source_id) in law_map
                    ),
                    "",
                )
                if chosen and chosen not in ordered_ids:
                    ordered_ids.append(chosen)

        application_lines: List[str] = []
        used_ids: List[str] = []
        for source_id in ordered_ids:
            law = law_map[source_id]
            excerpts = cls._source_sentences(str(law.get("text", "")), limit=2)
            if not excerpts:
                continue
            for excerpt in excerpts:
                application_lines.append(f"{excerpt.rstrip('.!?;:')} [{source_id}].")
            used_ids.append(source_id)

        if not application_lines:
            return ""

        return "\n".join([
            "OLUKORD:",
            "Mudeli vastus ei katnud kõiki tuvastatud küsimuse osi. Allpool kuvatakse ainult auditeeritud kohustustega seotud kontrollitud allikakatkendid.",
            "",
            "ÕIGUSLIK KOHALDAMINE:",
            *application_lines,
            "",
            "SOOVITUSED:",
            "Kontrolli otsuse või dokumendi täpset liiki, kuupäevi ja menetlejat, kui need võivad kohaldatavat menetlusteed muuta.",
            "",
            "KASUTATUD ALLIKAD: " + " ".join(f"[{source_id}]" for source_id in used_ids),
        ]).strip()

    @staticmethod
    def _source_sentences(text: str, *, limit: int) -> List[str]:
        normalized = str(text or "").replace("\r", "\n")
        parts = [
            re.sub(r"\s+", " ", value).strip()
            for value in re.split(r"(?<=[.!?])\s+|\n+", normalized)
            if re.sub(r"\s+", " ", value).strip()
        ]
        result: List[str] = []
        for value in parts:
            if len(value) > 700:
                value = value[:700].rstrip()
            if len(re.findall(r"[A-Za-zÕÄÖÜõäöü]{2,}", value)) < 3:
                continue
            result.append(value)
            if len(result) >= limit:
                break
        return result
