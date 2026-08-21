from __future__ import annotations

import copy
import unittest

from services.rt_model_context import RTModelContextError
from services.verified_live_analysis import VerifiedLiveModelAnalysisService
from tests.test_rt_model_context import live_record, local_candidate, local_fallback


class FakeLiveRetrieval:
    def __init__(self, laws):
        self.laws = list(laws)
        self.calls = []

    def upgrade_candidates(self, candidates, *, as_of):
        self.calls.append((list(candidates), as_of))
        verified = sum(
            1 for law in self.laws
            if law.get("verification_status") == "BINDING_SECTION_VERIFIED"
        )
        return {
            "version": "V11.4-rt-current-retrieval-1",
            "status": "LIVE_VERIFIED",
            "as_of_date": as_of.isoformat(),
            "laws": copy.deepcopy(self.laws),
            "verified_count": verified,
            "fallback_count": len(self.laws) - verified,
            "resolved_acts": [],
            "failures": [],
            "retrieval_enabled": True,
            "model_context_enabled": False,
            "corpus_write_enabled": False,
        }


class FakeAI:
    def __init__(self):
        self.calls = []

    def analyze_case_structured(self, case_desc, laws, event_date, document_spans=None):
        self.calls.append((case_desc, list(laws), event_date, list(document_spans or [])))
        source_id = laws[0]["id"]
        return (
            f"OLUKORD:\nTest.\n\nÕIGUSLIK KOHALDAMINE:\nTest [{source_id}]."
            f"\n\nKASUTATUD ALLIKAD: [{source_id}]",
            False,
            [],
        )


class VerifiedLiveModelAnalysisTests(unittest.TestCase):
    def test_live_verified_law_is_the_record_sent_to_model(self):
        live = live_record()
        fake_ai = FakeAI()
        service = VerifiedLiveModelAnalysisService(
            live_retrieval=FakeLiveRetrieval([live]),
            ai_service=fake_ai,
        )
        result = service.analyze_case_structured(
            "Kas suuline ülesütlemine kehtib?",
            [local_candidate()],
            "2026-08-21",
        )
        sent = fake_ai.calls[0][1]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["verification_status"], "BINDING_SECTION_VERIFIED")
        self.assertEqual(
            sent[0]["model_context_admission"],
            "VERIFIED_LIVE_BINDING_SECTION",
        )
        self.assertEqual(result["context"]["admission"]["live_count"], 1)

    def test_partial_live_result_can_keep_original_local_fallback(self):
        candidate = local_candidate()
        candidate_96 = {
            **candidate,
            "id": "TLS_96",
            "section": "96",
            "title": "Töölepingu seadus § 96",
        }
        fallback = local_fallback(candidate)
        fake_ai = FakeAI()
        service = VerifiedLiveModelAnalysisService(
            live_retrieval=FakeLiveRetrieval([live_record(section="96"), fallback]),
            ai_service=fake_ai,
        )
        context = service.prepare_context(
            [candidate, candidate_96],
            "2026-08-21",
        )
        admissions = {law["model_context_admission"] for law in context["laws"]}
        self.assertIn("VERIFIED_LIVE_BINDING_SECTION", admissions)
        self.assertIn("AUDITED_LOCAL_CORPUS_FALLBACK", admissions)

    def test_forged_local_fallback_never_reaches_model(self):
        candidate = local_candidate()
        fallback = local_fallback(candidate)
        fallback["text"] = "võltsitud"
        fake_ai = FakeAI()
        service = VerifiedLiveModelAnalysisService(
            live_retrieval=FakeLiveRetrieval([fallback]),
            ai_service=fake_ai,
        )
        with self.assertRaises(RTModelContextError):
            service.analyze_case_structured(
                "test",
                [candidate],
                "2026-08-21",
            )
        self.assertEqual(fake_ai.calls, [])

    def test_future_date_is_rejected_before_live_retrieval(self):
        fake_live = FakeLiveRetrieval([live_record()])
        service = VerifiedLiveModelAnalysisService(
            live_retrieval=fake_live,
            ai_service=FakeAI(),
        )
        with self.assertRaises(ValueError):
            service.prepare_context([local_candidate()], "2099-01-01")
        self.assertEqual(fake_live.calls, [])

    def test_empty_candidate_set_does_not_call_live_or_model(self):
        fake_live = FakeLiveRetrieval([])
        fake_ai = FakeAI()
        service = VerifiedLiveModelAnalysisService(
            live_retrieval=fake_live,
            ai_service=fake_ai,
        )
        context = service.prepare_context([], "2026-08-21")
        self.assertEqual(context["status"], "NO_CANDIDATES")
        self.assertEqual(fake_live.calls, [])
        with self.assertRaises(RTModelContextError):
            service.analyze_case_structured("test", [], "2026-08-21")
        self.assertEqual(fake_ai.calls, [])


if __name__ == "__main__":
    unittest.main()
