import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.analysis_orchestrator import AnalysisOrchestrator


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


if __name__ == "__main__":
    unittest.main()
