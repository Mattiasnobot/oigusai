from __future__ import annotations

import unittest

from services.verified_live_search import VerifiedLiveLegalSearch


class _LocalSearch:
    def __init__(self, laws):
        self.laws = laws

    def search_laws_with_context(self, query, event_date):
        return list(self.laws), {"query": query, "event_date": event_date}


class _LiveRetrieval:
    def __init__(self, result):
        self.result = result
        self.as_of = None

    def upgrade_candidates(self, candidates, *, as_of):
        self.as_of = as_of
        return dict(self.result)


class VerifiedLiveLegalSearchTests(unittest.TestCase):
    def test_empty_local_results_do_not_trigger_live_layer(self):
        live = _LiveRetrieval({"laws": []})
        result = VerifiedLiveLegalSearch(
            legal_search=_LocalSearch([]), live_retrieval=live,
        ).search_laws_with_context("küsimus", "2026-08-21")
        self.assertEqual(result["live"]["status"], "NO_LOCAL_CANDIDATES")
        self.assertIsNone(live.as_of)
        self.assertFalse(result["live"]["model_context_enabled"])

    def test_local_candidates_are_upgraded_for_exact_event_date(self):
        live = _LiveRetrieval({
            "version": "V11.4-rt-current-retrieval-1",
            "status": "LIVE_VERIFIED",
            "as_of_date": "2026-08-21",
            "laws": [{"id": "TLS_95", "evidence_source": "rt_live_verified"}],
            "verified_count": 1,
            "fallback_count": 0,
            "resolved_acts": [],
            "failures": [],
            "retrieval_enabled": True,
            "model_context_enabled": False,
            "corpus_write_enabled": False,
        })
        result = VerifiedLiveLegalSearch(
            legal_search=_LocalSearch([{"id": "TLS_95"}]), live_retrieval=live,
        ).search_laws_with_context("vallandamine", "2026-08-21")
        self.assertEqual(live.as_of.isoformat(), "2026-08-21")
        self.assertEqual(result["laws"][0]["evidence_source"], "rt_live_verified")
        self.assertNotIn("laws", result["live"])
        self.assertFalse(result["live"]["model_context_enabled"])

    def test_invalid_date_does_not_reach_live_layer(self):
        live = _LiveRetrieval({"laws": []})
        service = VerifiedLiveLegalSearch(
            legal_search=_LocalSearch([{"id": "TLS_95"}]), live_retrieval=live,
        )
        with self.assertRaises(ValueError):
            service.search_laws_with_context("vallandamine", "21.08.2026")
        self.assertIsNone(live.as_of)

    def test_future_date_is_rejected_before_live_layer(self):
        live = _LiveRetrieval({"laws": []})
        service = VerifiedLiveLegalSearch(
            legal_search=_LocalSearch([{"id": "TLS_95"}]), live_retrieval=live,
        )
        with self.assertRaises(ValueError):
            service.search_laws_with_context("vallandamine", "2099-01-01")
        self.assertIsNone(live.as_of)


if __name__ == "__main__":
    unittest.main()
