"""V11.5 explicit adapter: V11.4 verified retrieval -> model context -> existing AI gates."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Sequence

from services.offline_ai import OfflineAIService
from services.rt_current_retrieval import VerifiedRTLiveRetrievalService
from services.rt_model_context import (
    RTModelContextError,
    admit_model_context,
)


class VerifiedLiveModelAnalysisService:
    """Upgrade selected corpus candidates and send only admitted records to the model.

    This is intentionally an explicit adapter in V11.5. Normal application
    runtime is not switched to live network retrieval by default.
    """

    def __init__(self, *, live_retrieval=None, ai_service=None) -> None:
        self.live_retrieval = live_retrieval or VerifiedRTLiveRetrievalService()
        self.ai_service = ai_service or OfflineAIService()

    @staticmethod
    def _parse_event_date(event_date: str) -> date:
        if not event_date:
            return date.today()
        try:
            parsed = date.fromisoformat(str(event_date))
        except ValueError as exc:
            raise ValueError("Sündmuse kuupäev peab olema kujul YYYY-MM-DD.") from exc
        if parsed > date.today():
            raise ValueError("Tulevase kuupäeva live-mudelikonteksti ei verifitseerita.")
        return parsed

    def prepare_context(
        self,
        candidates: Sequence[Mapping[str, Any]],
        event_date: str = "",
    ) -> Dict[str, Any]:
        check_date = self._parse_event_date(event_date)
        if not candidates:
            return {
                "status": "NO_CANDIDATES",
                "laws": [],
                "live": {
                    "verified_count": 0,
                    "fallback_count": 0,
                    "failures": [],
                },
                "admission": {
                    "status": "EMPTY_CONTEXT",
                    "live_count": 0,
                    "local_count": 0,
                    "model_context_enabled": False,
                    "unverified_live_admitted": False,
                },
            }

        upgraded = self.live_retrieval.upgrade_candidates(
            candidates,
            as_of=check_date,
        )
        local_reference = {
            str(item.get("id", "")).strip().upper(): item
            for item in candidates
            if str(item.get("id", "")).strip()
        }
        admission = admit_model_context(
            upgraded.get("laws") or [],
            expected_as_of=check_date,
            local_reference=local_reference,
        )
        return {
            "status": "MODEL_CONTEXT_READY" if admission["laws"] else "NO_MODEL_CONTEXT",
            "laws": admission["laws"],
            "live": {key: value for key, value in upgraded.items() if key != "laws"},
            "admission": {key: value for key, value in admission.items() if key != "laws"},
        }

    def analyze_case_structured(
        self,
        case_desc: str,
        candidates: Sequence[Mapping[str, Any]],
        event_date: str = "",
        document_spans: Sequence[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        context = self.prepare_context(candidates, event_date)
        laws = context["laws"]
        if not laws:
            raise RTModelContextError("No admitted legal source is available for model context.")

        analysis, is_mock, claims = self.ai_service.analyze_case_structured(
            case_desc,
            laws,
            event_date,
            list(document_spans or []),
        )
        return {
            "analysis": analysis,
            "is_mock": is_mock,
            "claims": claims,
            "laws": laws,
            "context": {key: value for key, value in context.items() if key != "laws"},
        }
