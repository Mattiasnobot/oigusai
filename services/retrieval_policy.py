"""Deterministic legal retrieval routing policy.

This module contains only auditable routing decisions.  It does not search the
corpus, resolve section identifiers or decide whether a source is authoritative.
Every routed section still has to be resolved from the trusted legal corpus by
the analysis orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RetrievalPlan:
    """Inspectible routing result for one analysis request."""

    normalized_route_context: str
    document_route_ids: tuple[str, ...]
    intent_route_ids: tuple[str, ...]
    routed_ids: tuple[str, ...]
    employment_context: bool
    employment_form_question: bool


class RetrievalPolicy:
    """Build deterministic, auditable section-routing hints.

    The policy can suggest trusted-corpus IDs, but it never manufactures source
    content.  Callers must resolve every ID against the verified legal corpus.
    """

    @classmethod
    def plan(
        cls,
        *,
        case_description: str,
        search_text: str,
        current_intents: Sequence[str],
        fine_context: bool,
    ) -> RetrievalPlan:
        normalized_route_context = (
            f"{case_description}\n{search_text}"
        ).casefold()

        document_routes: list[str] = []
        is_explicit_warning_notice = (
            "hoiatustrahv" in normalized_route_context
            or "kirjalik hoiatamismenetlus" in normalized_route_context
            or (
                "mootorsõiduk" in normalized_route_context
                and "trahvitea" in normalized_route_context
            )
        )
        if is_explicit_warning_notice:
            document_routes = [
                "ABIPOLS_3",
                "ABIPOLS_16",
                "VTMS_19",
                "VTMS_54B2",
                "VTMS_54B5",
            ]
        elif (
            "lühimenetluse otsus" in normalized_route_context
            or "mõjutustrahv" in normalized_route_context
        ):
            document_routes = [
                "ABIPOLS_3",
                "ABIPOLS_16",
                "VTMS_19",
                "VTMS_54B9",
                "VTMS_54B11",
            ]
        elif "kiirmenetluse otsus" in normalized_route_context:
            document_routes = [
                "ABIPOLS_3",
                "ABIPOLS_16",
                "VTMS_19",
                "VTMS_57",
                "VTMS_114",
            ]

        intent_routes: list[str] = []
        intent_set = set(current_intents)
        if fine_context and "missed_deadline" in intent_set:
            intent_routes.extend(["VTMS_114", "VTMS_118"])
        elif fine_context and "challenge_decision" in intent_set:
            intent_routes.extend(["VTMS_19", "VTMS_114"])
        if fine_context and "payment_plan" in intent_set:
            intent_routes.extend(["KARS_66", "VTMS_57", "VTMS_74", "VTMS_204"])

        employment_context = any(
            term in normalized_route_context
            for term in ("valland", "töölepingu üles", "tööleping üles", "koond")
        ) or (
            "töölep" in normalized_route_context
            and "üles" in normalized_route_context
        )
        if employment_context:
            if "koond" in normalized_route_context:
                intent_routes.extend(["TLS_89", "TLS_97"])
            else:
                intent_routes.extend(["TLS_88", "TLS_95", "TLS_104"])

        routed_ids = tuple(dict.fromkeys([*document_routes, *intent_routes]))
        employment_form_question = employment_context and any(
            term in normalized_route_context
            for term in ("suulis", "kirjal", "vorminõ", "vormis", "vormi")
        )

        return RetrievalPlan(
            normalized_route_context=normalized_route_context,
            document_route_ids=tuple(document_routes),
            intent_route_ids=tuple(intent_routes),
            routed_ids=routed_ids,
            employment_context=employment_context,
            employment_form_question=employment_form_question,
        )
