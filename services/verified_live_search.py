"""V11.4 opt-in composition of corpus retrieval and verified RT live evidence."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict

from services.legal_search import LegalSearchService
from services.rt_current_retrieval import VerifiedRTLiveRetrievalService


class VerifiedLiveLegalSearch:
    """Use audited local retrieval for discovery, then verify selected evidence live."""

    def __init__(self, *, legal_search=None, live_retrieval=None) -> None:
        self.legal_search = legal_search or LegalSearchService()
        self.live_retrieval = live_retrieval or VerifiedRTLiveRetrievalService()

    @staticmethod
    def _parse_event_date(event_date: str) -> date:
        if not event_date:
            return date.today()
        try:
            parsed = date.fromisoformat(event_date)
        except ValueError as exc:
            raise ValueError("Sündmuse kuupäev peab olema kujul YYYY-MM-DD.") from exc
        if parsed > date.today():
            raise ValueError("Tulevase kuupäeva live-õigusseisu ei verifitseerita.")
        return parsed

    def search_laws_with_context(self, query: str, event_date: str = "") -> Dict[str, Any]:
        laws, interpretation = self.legal_search.search_laws_with_context(query, event_date)
        if not laws:
            return {
                "laws": [],
                "interpretation": interpretation,
                "live": {
                    "status": "NO_LOCAL_CANDIDATES",
                    "verified_count": 0,
                    "fallback_count": 0,
                    "failures": [],
                    "model_context_enabled": False,
                    "corpus_write_enabled": False,
                },
            }
        live = self.live_retrieval.upgrade_candidates(
            laws,
            as_of=self._parse_event_date(event_date),
        )
        return {
            "laws": live["laws"],
            "interpretation": interpretation,
            "live": {key: value for key, value in live.items() if key != "laws"},
        }
