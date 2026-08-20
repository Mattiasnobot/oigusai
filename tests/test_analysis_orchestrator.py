import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.analysis_orchestrator import AnalysisOrchestrator
from services.analysis_pipeline import AnalysisPipelineRun


async def immediate_work(_label, func, *args):
    return func(*args)


class AnalysisOrchestratorTests(unittest.TestCase):
    def test_prepare_routes_employment_sources_and_completes_first_stages(self):
        laws = [
            {"id": "TLS_88", "title": "TLS § 88", "text": "Ülesütlemine.", "source": "RT"},
            {"id": "TLS_95", "title": "TLS § 95", "text": "Kirjalik vorm.", "source": "RT"},
            {"id": "TLS_104", "title": "TLS § 104", "text": "Tühisus.", "source": "RT"},
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": [],
                "domain_hints": ["TLS"],
                "section_hints": [],
                "matches": [],
                "notes": [],
            }),
        )
        relevance = Mock()
        relevance.verify_laws.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        request = SimpleNamespace(
            case_description="Kas tööandja võib töölepingu suuliselt üles öelda?",
            current_message="Kas tööandja võib töölepingu suuliselt üles öelda?",
            answer_requirements=[],
            matter_id=None,
            document_ids=[],
            search_query=None,
            event_date=None,
        )

        prepared = asyncio.run(AnalysisOrchestrator(
            legal_service=legal_service,
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        ).prepare(request))

        self.assertEqual(
            [law["id"] for law in prepared.analysis_laws],
            ["TLS_88", "TLS_95", "TLS_104"],
        )
        self.assertTrue(prepared.route_plan.employment_form_question)
        pipeline = prepared.pipeline.public()
        self.assertEqual(
            [stage["status"] for stage in pipeline["stages"][:3]],
            ["completed", "completed", "completed"],
        )
        self.assertEqual(pipeline["stages"][3]["status"], "not_run")

    def test_prepare_keeps_exact_section_hint_narrow(self):
        laws = [
            {"id": "HMS_25", "title": "HMS § 25", "text": "Kättetoimetamine.", "source": "RT"},
            {"id": "TAIMKS_2B2", "title": "TaimeKS § 2²", "text": "Muu reegel.", "source": "RT"},
        ]
        legal_service = Mock()
        legal_service.search_laws_with_context.return_value = (
            laws,
            SimpleNamespace(to_dict=lambda: {
                "expanded_tokens": ["kättetoimetamine"],
                "domain_hints": ["HMS"],
                "section_hints": ["HMS_25"],
                "matches": [],
                "notes": [],
            }),
        )
        relevance = Mock()
        relevance.verify_laws.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        request = SimpleNamespace(
            case_description="Kuidas haldusorgan otsuse kätte toimetab?",
            current_message="",
            answer_requirements=[],
            matter_id=None,
            document_ids=[],
            search_query=None,
            event_date=None,
        )

        prepared = asyncio.run(AnalysisOrchestrator(
            legal_service=legal_service,
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        ).prepare(request))

        self.assertEqual([law["id"] for law in prepared.analysis_laws], ["HMS_25"])
        self.assertEqual(prepared.query_context["section_hints"], ["HMS_25"])

    def test_execute_returns_source_verified_model_answer(self):
        laws = [{
            "id": "HMS_25",
            "title": "HMS § 25",
            "text": "Dokument toimetatakse kätte postiga.",
            "source": "RT",
        }]
        legal_service = Mock()
        relevance = Mock()
        relevance.verify_answer.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        orchestrator = AnalysisOrchestrator(
            legal_service=legal_service,
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        )
        prepared = SimpleNamespace(
            pipeline=AnalysisPipelineRun(),
            current_turn="",
            answer_requirements=[],
            document_spans=[],
            relevance_text="kättetoimetamine",
            route_plan=SimpleNamespace(employment_form_question=False),
            analysis_laws=laws,
        )
        for stage in ("case_understanding", "document_evidence", "legal_retrieval"):
            prepared.pipeline.complete(stage)
        request = SimpleNamespace(
            case_description="Kuidas otsus kätte toimetatakse?",
            case_context=None,
            event_date=None,
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "ÕIGUSLIK KOHALDAMINE:\nDokument toimetatakse kätte postiga [HMS_25].",
            False,
        )
        source_verifier = Mock()
        source_verifier.verify_sources.return_value = (True, ["HMS_25"])

        executed = asyncio.run(orchestrator.execute(
            request,
            prepared,
            ai_service=ai_service,
            source_verifier=source_verifier,
        ))

        self.assertFalse(executed.fallback_used)
        self.assertEqual(executed.verified_sources, ["HMS_25"])
        self.assertEqual([law["id"] for law in executed.analysis_laws], ["HMS_25"])
        pipeline = prepared.pipeline.public()
        self.assertEqual(pipeline["stages"][3]["status"], "completed")
        self.assertEqual(pipeline["stages"][4]["status"], "completed")

    def test_execute_enforces_tls_95_form_coverage(self):
        laws = [
            {"id": "TLS_88", "title": "TLS § 88", "text": "Ülesütlemine.", "source": "RT"},
            {
                "id": "TLS_95",
                "title": "TLS § 95",
                "text": (
                    "Ülesütlemisavaldus peab olema kirjalikku taasesitamist võimaldavas "
                    "vormis. Vorminõuet rikkudes tehtud avaldus on tühine."
                ),
                "source": "RT",
            },
            {"id": "TLS_104", "title": "TLS § 104", "text": "Tühisus.", "source": "RT"},
        ]
        relevance = Mock()
        relevance.verify_answer.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        orchestrator = AnalysisOrchestrator(
            legal_service=Mock(),
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        )
        prepared = SimpleNamespace(
            pipeline=AnalysisPipelineRun(),
            current_turn="Kas võib suuliselt üles öelda?",
            answer_requirements=[],
            document_spans=[],
            relevance_text="töölepingu suuline ülesütlemine",
            route_plan=SimpleNamespace(employment_form_question=True),
            analysis_laws=laws,
        )
        for stage in ("case_understanding", "document_evidence", "legal_retrieval"):
            prepared.pipeline.complete(stage)
        request = SimpleNamespace(
            case_description="Kas tööandja võib töölepingu suuliselt üles öelda?",
            case_context=None,
            event_date=None,
        )
        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "ÕIGUSLIK KOHALDAMINE:\nTööandja võib mõjuval põhjusel üles öelda [TLS_88].",
            False,
        )
        source_verifier = Mock()
        source_verifier.verify_sources.return_value = (True, ["TLS_95"])

        executed = asyncio.run(orchestrator.execute(
            request,
            prepared,
            ai_service=ai_service,
            source_verifier=source_verifier,
        ))

        self.assertTrue(executed.fallback_used)
        self.assertTrue(executed.coverage_fallback_used)
        self.assertEqual(executed.verified_sources, ["TLS_95"])
        self.assertEqual([law["id"] for law in executed.analysis_laws], ["TLS_95"])
        self.assertIn("kirjalikku taasesitamist võimaldavas vormis", executed.analysis_text)
        self.assertIn("on tühine", executed.analysis_text)

    def test_finalize_verifies_evidence_packages_answer_and_records_metrics(self):
        orchestrator = AnalysisOrchestrator(
            legal_service=Mock(),
            matter_store=None,
            relevance_verifier=Mock(),
            run_guarded_work=immediate_work,
        )
        pipeline = AnalysisPipelineRun()
        for stage in (
            "case_understanding",
            "document_evidence",
            "legal_retrieval",
            "model_analysis",
            "source_verification",
        ):
            pipeline.complete(stage)
        prepared = SimpleNamespace(
            analysis_started=0.0,
            pipeline=pipeline,
            document_spans=[],
            case_card={},
        )
        claim = {
            "claim_id": "LAW-1",
            "kind": "legal",
            "text": "Dokument toimetatakse kätte.",
            "verification_status": "EVIDENCE_VERIFIED",
            "sources": [{"id": "HMS_25"}],
        }
        executed = SimpleNamespace(
            analysis_laws=[{
                "id": "HMS_25",
                "title": "HMS § 25",
                "text": "Dokument toimetatakse kätte.",
                "source": "RT",
            }],
            document_claims=[],
            structured_claims=[claim],
            analysis_text="Dokument toimetatakse kätte [HMS_25].",
            is_mock=False,
            fallback_used=False,
            coverage_fallback_used=False,
            verified_sources=["HMS_25"],
        )
        request = SimpleNamespace(
            case_description="Kuidas otsus kätte toimetatakse?",
            event_date=None,
        )
        evidence_verifier = Mock()
        evidence_verifier.verify.return_value = (True, [claim])
        urgency_analyzer = Mock()
        urgency_analyzer.analyze.return_value = {"urgent": False, "questions": []}
        verified_answer_builder = Mock()
        verified_answer_builder.build.return_value = {
            "confidence": "supported",
            "unknowns": [],
        }
        metrics_store = Mock()

        finalized = orchestrator.finalize(
            request,
            prepared,
            executed,
            evidence_verifier=evidence_verifier,
            urgency_analyzer=urgency_analyzer,
            verified_answer_builder=verified_answer_builder,
            metrics_store=metrics_store,
        )

        self.assertEqual(finalized["verification_status"], "EVIDENCE_VERIFIED")
        self.assertEqual(finalized["verified_sources"], ["HMS_25"])
        self.assertEqual(finalized["combined_claims"], [claim])
        self.assertEqual(finalized["pipeline"]["status"], "completed")
        metrics_store.record_analysis.assert_called_once()


if __name__ == "__main__":
    unittest.main()
