from __future__ import annotations

import unittest
from unittest.mock import patch

from config import load_settings
from services.offline_ai import OfflineAIService
from services.verified_live_ai import VerifiedLiveOfflineAIService
from tests.test_rt_model_context import live_record, local_candidate


class FakeContextAdapter:
    def __init__(self, *, laws=None, error=None):
        self.laws = list(laws or [])
        self.error = error
        self.calls = []

    def prepare_context(self, candidates, event_date=""):
        self.calls.append((candidates, event_date))
        if self.error:
            raise self.error
        return {
            "status": "MODEL_CONTEXT_READY" if self.laws else "NO_MODEL_CONTEXT",
            "laws": [dict(item) for item in self.laws],
            "live": {"status": "LIVE_VERIFIED"},
            "admission": {
                "status": "VERIFIED_LIVE_CONTEXT" if self.laws else "EMPTY_CONTEXT",
                "live_count": len(self.laws),
                "local_count": 0,
                "model_context_enabled": bool(self.laws),
                "unverified_live_admitted": False,
            },
        }


class VerifiedLiveRuntimeWiringTests(unittest.TestCase):
    def test_config_defaults_disabled_and_can_be_enabled(self):
        self.assertFalse(load_settings({}).rt_verified_live_model_context_enabled)
        self.assertTrue(
            load_settings({"RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED": "true"})
            .rt_verified_live_model_context_enabled
        )

    def test_runtime_wrapper_remains_an_offline_ai_service(self):
        self.assertTrue(issubclass(VerifiedLiveOfflineAIService, OfflineAIService))

    def test_disabled_wrapper_never_calls_live_context_adapter(self):
        adapter = FakeContextAdapter(error=AssertionError("must not be called"))
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=False,
            live_context_adapter=adapter,
        )
        laws = [local_candidate()]
        with patch.object(
            OfflineAIService,
            "analyze_case_structured",
            return_value=("ok", False, []),
        ):
            result = service.analyze_case_structured(
                "case", laws, "2026-08-21", []
            )
        self.assertEqual(result, ("ok", False, []))
        self.assertEqual(adapter.calls, [])
        self.assertNotIn("model_context_admission", laws[0])

    def test_enabled_wrapper_mutates_same_list_before_parent_model_call(self):
        admitted = live_record()
        admitted["model_context_enabled"] = True
        admitted["model_context_version"] = "V11.5-verified-live-model-context-1"
        admitted["model_context_admission"] = "VERIFIED_LIVE_BINDING_SECTION"
        adapter = FakeContextAdapter(laws=[admitted])
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
        )
        laws = [local_candidate()]
        original_identity = id(laws)
        with patch.object(
            OfflineAIService,
            "analyze_case_structured",
            return_value=("ok", False, []),
        ):
            result = service.analyze_case_structured(
                "case", laws, "2026-08-21", []
            )
        self.assertEqual(result, ("ok", False, []))
        self.assertEqual(id(laws), original_identity)
        self.assertEqual(laws[0]["verification_status"], "BINDING_SECTION_VERIFIED")
        self.assertEqual(
            laws[0]["model_context_admission"],
            "VERIFIED_LIVE_BINDING_SECTION",
        )
        self.assertEqual(
            service.last_live_model_context["admission"]["live_count"], 1
        )

    def test_admission_failure_keeps_original_audited_local_laws(self):
        adapter = FakeContextAdapter(error=RuntimeError("forged live record"))
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
        )
        laws = [local_candidate()]
        original_text = laws[0]["text"]
        with patch.object(
            OfflineAIService,
            "analyze_case_structured",
            return_value=("ok", False, []),
        ):
            service.analyze_case_structured("case", laws, "2026-08-21", [])
        self.assertEqual(laws[0]["text"], original_text)
        self.assertNotIn("model_context_admission", laws[0])
        self.assertEqual(
            service.last_live_model_context["status"], "LOCAL_MODEL_CONTEXT"
        )
        self.assertFalse(service.last_live_model_context["model_context_enabled"])

    def test_empty_admission_keeps_original_local_laws(self):
        adapter = FakeContextAdapter(laws=[])
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
        )
        laws = [local_candidate()]
        with patch.object(
            OfflineAIService,
            "analyze_case_structured",
            return_value=("ok", False, []),
        ):
            service.analyze_case_structured("case", laws, "2026-08-21", [])
        self.assertEqual(laws[0]["id"], "TLS_95")
        self.assertNotIn("model_context_admission", laws[0])
        self.assertEqual(
            service.last_live_model_context["reason"], "no_admitted_live_context"
        )

    def test_main_runtime_import_points_to_verified_wrapper(self):
        import main

        self.assertIs(main.OfflineAIService, VerifiedLiveOfflineAIService)


if __name__ == "__main__":
    unittest.main()
