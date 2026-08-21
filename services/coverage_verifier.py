"""Deterministic obligation coverage verification for ÕigusAI V10.4.

Coverage is deliberately narrower than legal reasoning.  The verifier checks
whether an audited retrieval obligation has at least one trusted source in the
model context and whether the final cited answer actually uses the required
source group.  It never creates a new authority and it never treats model
memory as a legal source.

Audited obligation -> source-group rules live in services.policy_registry.
CoverageVerifier consumes that versioned registry without owning policy metadata.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from services.policy_registry import CoveragePolicyRegistry


class CoverageVerifier:
    """Verify retrieval and answer coverage without making legal conclusions."""

    STATUS_COVERED = "COVERED"
    STATUS_ANSWER_MISSING = "ANSWER_MISSING"
    STATUS_SOURCE_MISSING = "SOURCE_MISSING"
    STATUS_RULE_UNRESOLVED = "RULE_UNRESOLVED"

    @staticmethod
    def _normalize_id(value: Any) -> str:
        return re.sub(r"[^A-ZÕÄÖÜ0-9_]", "", str(value or "").strip().upper())

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    @classmethod
    def verify(
        cls,
        obligation_plan: Any,
        available_laws: Iterable[Dict[str, Any]],
        verified_sources: Iterable[str],
        *,
        answer_text: Any = None,
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
        normalized_answer = (
            cls._normalize_text(answer_text) if answer_text is not None else ""
        )

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
            rule = CoveragePolicyRegistry.get(reason)
            if rule is None:
                unresolved_rules.append(kind)
                rows.append({
                    "kind": kind,
                    "reason": reason,
                    "rule_id": "",
                    "answer_requirement": answer_requirement,
                    "enforced": False,
                    "status": cls.STATUS_RULE_UNRESOLVED,
                    "expected_source_groups": [],
                    "candidate_sources": [],
                    "cited_sources": [],
                    "required_answer_terms": [],
                    "missing_answer_terms": [],
                    "rationale": "Auditeeritud coverage-policy registry's puudub sellele kohustusele reegel.",
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
            normalized_answer_terms = tuple(
                tuple(
                    cls._normalize_text(value)
                    for value in group
                    if cls._normalize_text(value)
                )
                for group in rule.required_answer_terms
            )
            missing_answer_term_groups: List[Tuple[str, ...]] = []
            if answer_text is not None:
                missing_answer_term_groups = [
                    group
                    for group in normalized_answer_terms
                    if group and not any(term in normalized_answer for term in group)
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
            elif answer_missing_groups or missing_answer_term_groups:
                status = cls.STATUS_ANSWER_MISSING
                if kind not in missing_answer:
                    missing_answer.append(kind)
            else:
                status = cls.STATUS_COVERED
                covered_count += 1

            rows.append({
                "kind": kind,
                "reason": reason,
                "rule_id": rule.rule_id,
                "answer_requirement": answer_requirement,
                "enforced": True,
                "status": status,
                "expected_source_groups": [list(group) for group in normalized_groups],
                "candidate_sources": candidate_sources,
                "cited_sources": cited_sources,
                "required_answer_terms": [
                    list(group) for group in normalized_answer_terms
                ],
                "missing_answer_terms": [
                    list(group) for group in missing_answer_term_groups
                ],
                "rationale": rule.rationale,
            })

        passed = not missing_answer and not missing_source
        return {
            "policy_registry_version": CoveragePolicyRegistry.VERSION,
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
    def repair_targets(cls, report: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Return ordered audited targets for one bounded model-repair pass."""
        if report.get("missing_source"):
            return []
        targets: List[Dict[str, Any]] = []
        for row in report.get("obligations") or []:
            if not isinstance(row, dict) or not row.get("enforced"):
                continue
            candidate_sources = [
                cls._normalize_id(value)
                for value in (row.get("candidate_sources") or [])
                if cls._normalize_id(value)
            ]
            if not candidate_sources:
                continue
            targets.append({
                "kind": str(row.get("kind") or ""),
                "answer_requirement": str(
                    row.get("answer_requirement") or row.get("kind") or ""
                ),
                "source_id": candidate_sources[0],
                "required_answer_terms": [
                    [str(value).strip() for value in group if str(value).strip()]
                    for group in (row.get("required_answer_terms") or [])
                    if isinstance(group, (list, tuple))
                ],
            })
        return targets

    @classmethod
    def repair_schema(cls, report: Mapping[str, Any]) -> Dict[str, Any]:
        """Constrain Ollama repair output to the audited target count and IDs."""
        targets = cls.repair_targets(report)
        source_ids = list(dict.fromkeys(
            str(target.get("source_id") or "")
            for target in targets
            if str(target.get("source_id") or "")
        ))
        count = len(targets)
        if not count or not source_ids:
            return {}
        return {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "source_id": {"type": "string", "enum": source_ids},
                            "evidence": {"type": "string"},
                        },
                        "required": ["text", "source_id", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["claims"],
            "additionalProperties": False,
        }

    @classmethod
    def repair_prompt(
        cls,
        report: Mapping[str, Any],
        laws: Sequence[Dict[str, Any]],
        case_desc: str,
        event_date: str = "",
    ) -> str:
        """Build a minimal repair-only prompt without the general analysis rules."""
        targets = cls.repair_targets(report)
        if not targets:
            return ""
        law_map = {
            cls._normalize_id(law.get("id")): law
            for law in (laws or [])
            if cls._normalize_id(law.get("id"))
        }
        source_lines: List[str] = []
        for target in targets:
            source_id = str(target.get("source_id") or "")
            law = law_map.get(source_id)
            if law is None:
                return ""
            source_lines.append(
                f"[{source_id}] {law.get('title', source_id)}: {law.get('text', '')}"
            )

        target_lines: List[str] = []
        for index, target in enumerate(targets, start=1):
            target_lines.extend([
                f"KOHUSTUS {index}/{len(targets)} [{target.get('kind', '')}]",
                f"- vasta: {target.get('answer_requirement', '')}",
                f"- source_id peab olema täpselt: {target.get('source_id', '')}",
            ])
            for group in target.get("required_answer_terms") or []:
                terms = [str(value).strip() for value in group if str(value).strip()]
                if terms:
                    target_lines.append(
                        "- claim tekst peab sisaldama vähemalt üht markerit: "
                        + " / ".join(terms)
                    )

        event_line = f"Sündmuse kuupäev: {event_date}\n" if event_date else ""
        return "\n".join([
            "Sa parandad ainult ÕigusAI auditeeritud vastusekatvust.",
            "Kasuta AINULT allpool antud kontrollitud allikaid.",
            "Ära vasta kõrvalteemadele ja ära lisa mudeli mälust ühtegi õigusväidet.",
            "",
            event_line.rstrip(),
            "JUHTUM:",
            str(case_desc or "").strip(),
            "",
            "TÄIDETAVAD KOHUSTUSED:",
            *target_lines,
            "",
            "LUBATUD ALLIKAD:",
            *source_lines,
            "",
            f"Tagasta claims massiivis TÄPSELT {len(targets)} elementi samas järjekorras.",
            "Iga claim kasutab talle määratud source_id-d.",
            "evidence peab olema sama allika tekstist täpselt kopeeritud katkematu katkend.",
            "claim peab olema evidence otsene ja kitsas ümbersõnastus; kui kahtled, kasuta claim tekstina evidence teksti.",
            "Tagasta ainult JSON, ilma Markdowni või muu tekstita.",
        ]).replace("\n\n\n", "\n\n").strip()

    @classmethod
    def repair_laws(
        cls,
        report: Mapping[str, Any],
        laws: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return only audited laws needed by the model coverage-repair pass."""
        if report.get("missing_source"):
            return []
        law_map = {
            cls._normalize_id(law.get("id")): law
            for law in (laws or [])
            if cls._normalize_id(law.get("id"))
        }
        ordered_ids = [
            str(target.get("source_id") or "")
            for target in cls.repair_targets(report)
            if str(target.get("source_id") or "") in law_map
        ]
        return [law_map[source_id] for source_id in dict.fromkeys(ordered_ids)]

    @classmethod
    def repair_instructions(cls, report: Mapping[str, Any]) -> str:
        """Build deterministic instructions for one model coverage-repair attempt."""
        rows = [
            row for row in (report.get("obligations") or [])
            if isinstance(row, dict) and row.get("enforced")
        ]
        if not rows or not report.get("needs_repair"):
            return ""

        targets: List[Tuple[str, str, str, List[List[str]]]] = []
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
                [
                    list(group)
                    for group in (row.get("missing_answer_terms") or [])
                    if isinstance(group, (list, tuple))
                ],
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
        for index, (kind, requirement, source_id, missing_terms) in enumerate(targets, start=1):
            lines.extend([
                f"KOHUSTUS {index}/{len(targets)} [{kind}]",
                f"- küsimus: {requirement}",
                f"- source_id: {source_id}",
            ])
            for group in missing_terms:
                terms = [str(value).strip() for value in group if str(value).strip()]
                if terms:
                    lines.append(
                        "- coverage_check: kasuta allika sõnastust nii, et vastuses "
                        "esineks fraas " + " / ".join(f'\"{term}\"' for term in terms)
                    )
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
