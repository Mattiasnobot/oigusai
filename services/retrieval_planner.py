"""Deterministic multi-issue retrieval planning for ÕigusAI V10.2.

The planner decomposes one user request into inspectable retrieval obligations.
It does not choose legal authorities and it never creates source content.  Each
query must still be resolved through LegalSearchService and the trusted corpus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RetrievalObligation:
    """One auditable legal question that deserves its own retrieval pass."""

    kind: str
    query: str
    answer_requirement: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "query": self.query,
            "answer_requirement": self.answer_requirement,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MultiIssueRetrievalPlan:
    """Ordered obligation plan for one analysis request."""

    obligations: tuple[RetrievalObligation, ...]

    @property
    def multi_issue(self) -> bool:
        return len(self.obligations) > 1

    @property
    def answer_requirements(self) -> tuple[str, ...]:
        return tuple(item.answer_requirement for item in self.obligations)

    def to_dict(self) -> dict[str, object]:
        return {
            "multi_issue": self.multi_issue,
            "obligations": [item.to_dict() for item in self.obligations],
        }


class MultiIssueRetrievalPlanner:
    """Decompose natural-language requests into deterministic retrieval queries."""

    _ORDER = (
        "authority",
        "procedure",
        "deadline",
        "remedy",
        "payment",
        "form_requirement",
    )

    _REQUIREMENTS = {
        "authority": "milline asutus või kohus on pädev ja kellele tuleb pöörduda",
        "procedure": "milline menetlus või õiguslik alus kirjeldatud olukorrale kohaldub",
        "deadline": "milline tähtaeg kohaldub või kas möödunud tähtaega saab ennistada",
        "remedy": "milline vaidlustamise või muu õiguskaitsevahend on kasutajal olemas",
        "payment": "kas rahatrahvi saab tasuda ositi ja millised täitmise tagajärjed võivad järgneda",
        "form_requirement": "milline vorminõue kehtib ja mis on vorminõude rikkumise tagajärg",
    }

    @classmethod
    def plan(
        cls,
        *,
        case_description: str,
        search_text: str,
        current_intents: Sequence[str],
        answer_requirements: Sequence[str] = (),
        fine_context: bool = False,
    ) -> MultiIssueRetrievalPlan:
        context = f"{case_description}\n{search_text}\n{' '.join(answer_requirements)}".casefold()
        intents = set(current_intents)
        employment_context = cls._employment_context(context)
        planned: dict[str, RetrievalObligation] = {}

        def add(kind: str, query: str, reason: str, *, replace: bool = False) -> None:
            if kind in planned and not replace:
                return
            cleaned = " ".join(str(query or "").split())[:1200].strip()
            if not cleaned:
                return
            planned[kind] = RetrievalObligation(
                kind=kind,
                query=cleaned,
                answer_requirement=cls._REQUIREMENTS[kind],
                reason=reason,
            )

        generic_scope = cls._scope_prefix(
            context=context,
            search_text=search_text,
            fine_context=fine_context,
            employment_context=employment_context,
        )

        if "find_authority" in intents:
            add(
                "authority",
                f"{generic_scope} pädev asutus kohus kellele esitada kuhu pöörduda",
                "latest_turn_intent:find_authority",
            )
        if intents.intersection({"document_help", "next_steps"}):
            add(
                "procedure",
                f"{generic_scope} kohaldatav menetlus avaldus taotlus järgmised sammud",
                "latest_turn_intent:procedure",
            )
        if "deadline" in intents:
            add(
                "deadline",
                f"{generic_scope} kohaldatav tähtaeg tähtaja arvestamine",
                "latest_turn_intent:deadline",
            )
        if "challenge_decision" in intents:
            add(
                "remedy",
                f"{generic_scope} otsuse vaidlustamine kaebus vaie õiguskaitsevahend",
                "latest_turn_intent:challenge_decision",
            )
        if "payment_plan" in intents:
            add(
                "payment",
                f"{generic_scope} tasumine ositi osamaksed maksegraafik täitmisele pööramine",
                "latest_turn_intent:payment_plan",
            )

        if fine_context:
            if "missed_deadline" in intents or cls._missed_deadline_context(context):
                add(
                    "deadline",
                    "rahatrahv väärteomenetlus kaebuse tähtaja ennistamine tähtaja möödumine läbi vaatamata",
                    "fine_context:missed_deadline",
                    replace=True,
                )
            if "challenge_decision" in intents:
                add(
                    "remedy",
                    "rahatrahv väärteomenetluse otsuse vaidlustamine kaebus maakohtule menetlusaluse isiku õigused",
                    "fine_context:challenge_decision",
                    replace=True,
                )
            if "payment_plan" in intents:
                add(
                    "payment",
                    "rahatrahvi tasumine ositi mõjuv põhjus osamaksed kohtuväline menetleja täitmisele pööramine",
                    "fine_context:payment_plan",
                    replace=True,
                )

        if employment_context and "koond" in context:
            add(
                "procedure",
                "töölepingu ülesütlemine koondamise tõttu töömahu vähenemine töö ümberkorraldamine teise töö pakkumine",
                "employment_context:redundancy_basis",
                replace=True,
            )
            if "deadline" in intents or "etteteat" in context or "kui pikk" in context:
                add(
                    "deadline",
                    "töölepingu ülesütlemise etteteatamise tähtaeg koondamine töötaja",
                    "employment_context:notice_period",
                    replace=True,
                )

        if employment_context and any(
            term in context for term in ("suulis", "kirjal", "vorminõ", "vormis", "vormi")
        ):
            add(
                "form_requirement",
                "töölepingu ülesütlemisavaldus kirjalikku taasesitamist võimaldav vorm tühine",
                "employment_context:termination_form",
                replace=True,
            )

        ordered = tuple(planned[kind] for kind in cls._ORDER if kind in planned)
        return MultiIssueRetrievalPlan(obligations=ordered)

    @staticmethod
    def _missed_deadline_context(context: str) -> bool:
        has_deadline = "tähta" in context or "tahta" in context
        return has_deadline and any(
            marker in context
            for marker in ("mööda", "moodas", "ület", "ulet", "hiljaks", "ennist")
        )

    @staticmethod
    def _employment_context(context: str) -> bool:
        return any(
            term in context
            for term in ("töölep", "töötaja", "tööandja", "valland", "koond")
        )

    @staticmethod
    def _scope_prefix(
        *,
        context: str,
        search_text: str,
        fine_context: bool,
        employment_context: bool,
    ) -> str:
        if fine_context:
            return "rahatrahv väärteomenetlus"
        if employment_context:
            return "tööleping töötaja tööandja"
        return " ".join(str(search_text or context).split())[:500]
