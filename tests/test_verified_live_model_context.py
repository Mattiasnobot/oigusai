from __future__ import annotations

import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from config import load_settings
from services.offline_ai import OfflineAIService
from services.analysis_orchestrator import AnalysisOrchestrator
from services.analysis_pipeline import AnalysisPipelineRun
from services.rt_model_context import RTModelContextError
from services.verified_live_ai import VerifiedLiveOfflineAIService
from tests.test_rt_model_context import live_record, local_candidate
from verifiers.source_verifier import SourceVerifier


async def immediate_work(_label, func, *args):
    return func(*args)


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
        adapter = FakeContextAdapter(error=RTModelContextError("forged live record"))
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
        self.assertEqual(service.live_model_context_stats()["local_fallback"], 1)

    def test_unexpected_adapter_defect_is_not_silently_downgraded(self):
        adapter = FakeContextAdapter(error=TypeError("adapter contract defect"))
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
        )
        with self.assertRaisesRegex(TypeError, "adapter contract defect"):
            service.analyze_case_structured(
                "case", [local_candidate()], "2026-08-21", []
            )
        self.assertEqual(service.live_model_context_stats()["unexpected_error"], 1)

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

    def test_multiple_and_mixed_admitted_records_preserve_order(self):
        live = live_record()
        live["model_context_admission"] = "VERIFIED_LIVE_BINDING_SECTION"
        local = local_candidate()
        local["model_context_admission"] = "AUDITED_LOCAL_CORPUS_FALLBACK"
        adapter = FakeContextAdapter(laws=[live, local])
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
        )
        laws = [local_candidate(), {**local_candidate(), "id": "TLS_96"}]
        with patch.object(
            OfflineAIService,
            "analyze_case_structured",
            return_value=("ok", False, []),
        ):
            service.analyze_case_structured("case", laws, "2026-08-21", [])
        self.assertEqual(
            [law["model_context_admission"] for law in laws],
            ["VERIFIED_LIVE_BINDING_SECTION", "AUDITED_LOCAL_CORPUS_FALLBACK"],
        )

    def test_invalid_and_future_dates_keep_local_context(self):
        for event_date in ("not-a-date", "2999-01-01"):
            with self.subTest(event_date=event_date):
                service = VerifiedLiveOfflineAIService(
                    settings=load_settings({}),
                    live_model_context_enabled=True,
                )
                laws = [local_candidate()]
                with patch.object(
                    OfflineAIService,
                    "analyze_case_structured",
                    return_value=("ok", False, []),
                ):
                    service.analyze_case_structured("case", laws, event_date, [])
                self.assertNotIn("model_context_admission", laws[0])
                self.assertEqual(
                    service.last_live_model_context["status"], "LOCAL_MODEL_CONTEXT"
                )

    def test_diagnostics_are_isolated_between_request_threads(self):
        admitted = live_record()
        admitted["model_context_admission"] = "VERIFIED_LIVE_BINDING_SECTION"
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=FakeContextAdapter(laws=[admitted]),
        )

        def run_one():
            laws = [local_candidate()]
            with patch.object(
                OfflineAIService,
                "analyze_case_structured",
                return_value=("ok", False, []),
            ):
                service.analyze_case_structured("case", laws, "2026-08-21", [])
            return service.last_live_model_context["status"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _value: run_one(), range(2)))
        self.assertEqual(statuses, ["MODEL_CONTEXT_READY", "MODEL_CONTEXT_READY"])
        self.assertEqual(service.last_live_model_context["status"], "DISABLED")
        self.assertEqual(service.live_model_context_stats()["admitted"], 2)

    def test_normal_orchestrator_uses_admitted_records_for_model_and_verifier(self):
        admitted = live_record()
        admitted["text"] = "LIVE VERIFIED SECTION TEXT"
        admitted["model_context_admission"] = "VERIFIED_LIVE_BINDING_SECTION"
        adapter = FakeContextAdapter(laws=[admitted])
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_adapter=adapter,
            allow_mock=False,
        )
        raw = json.dumps({
            "claims": [{
                "text": "LIVE VERIFIED SECTION TEXT",
                "source_id": "TLS_95",
                "evidence": "LIVE VERIFIED SECTION TEXT",
            }],
        })
        relevance = Mock()
        relevance.verify_answer.return_value = SimpleNamespace(
            relevant=True, missing_concepts=[], clarification=""
        )
        orchestrator = AnalysisOrchestrator(
            legal_service=Mock(),
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        )
        pipeline = AnalysisPipelineRun()
        for stage in ("case_understanding", "document_evidence", "legal_retrieval"):
            pipeline.complete(stage)
        prepared = SimpleNamespace(
            pipeline=pipeline,
            current_turn="",
            answer_requirements=[],
            obligation_plan=None,
            document_spans=[],
            relevance_text="live verified section",
            route_plan=SimpleNamespace(employment_form_question=False),
            analysis_laws=[local_candidate()],
        )
        request = SimpleNamespace(
            case_description="case", case_context=None, event_date="2026-08-21"
        )
        verifier = Mock(wraps=SourceVerifier())
        with patch.object(service, "_call_ollama", return_value=raw) as model_call:
            executed = asyncio.run(orchestrator.execute(
                request,
                prepared,
                ai_service=service,
                source_verifier=verifier,
            ))

        prompt = model_call.call_args.args[0]
        self.assertIn("LIVE VERIFIED SECTION TEXT", prompt)
        self.assertNotIn(local_candidate()["text"], prompt)
        verifier_laws = verifier.verify_sources.call_args_list[0].args[1]
        self.assertIs(verifier_laws, executed.analysis_laws)
        self.assertEqual(
            executed.analysis_laws[0]["model_context_admission"],
            "VERIFIED_LIVE_BINDING_SECTION",
        )

    def test_main_runtime_import_points_to_verified_wrapper(self):
        import main

        self.assertIs(main.OfflineAIService, VerifiedLiveOfflineAIService)


if __name__ == "__main__":
    unittest.main()
