import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.analysis_orchestrator import AnalysisOrchestrator
from services.analysis_pipeline import AnalysisPipelineRun
from services.coverage_verifier import CoverageVerifier
from services.offline_ai import OfflineAIService
from services.retrieval_planner import MultiIssueRetrievalPlanner
from verifiers.source_verifier import SourceVerifier


async def immediate_work(_label, func, *args):
    return func(*args)


class _RepairingOfflineAI(OfflineAIService):
    def __init__(self):
        self.calls = 0

    def analyze_case_structured(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                "ÕIGUSLIK KOHALDAMINE:\nKoondamise alus on kirjeldatud sättes [TLS_89].\n\n"
                "SOOVITUSED:\nSäilita dokumendid.\n\nKASUTATUD ALLIKAD: [TLS_89]",
                False,
                [],
            )
        return (
            "ÕIGUSLIK KOHALDAMINE:\nKoondamise alus on kirjeldatud sättes [TLS_89].\n"
            "Etteteatamise tähtaeg on kirjeldatud sättes [TLS_97].\n\n"
            "SOOVITUSED:\nSäilita dokumendid.\n\nKASUTATUD ALLIKAD: [TLS_89] [TLS_97]",
            False,
            [],
        )

    def claims_from_verified_analysis(self, *_args, **_kwargs):
        return []

    def build_source_only_fallback(self, *_args, **_kwargs):
        raise AssertionError("Coverage repair should succeed before fallback")


class CoverageVerifierTests(unittest.TestCase):
    def test_redundancy_deadline_is_reported_missing_from_answer(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="Mind koondatakse. Kui pikk etteteatamine on?",
            current_intents=["deadline"],
            fine_context=False,
        )
        report = CoverageVerifier.verify(
            plan,
            [
                {"id": "TLS_89", "text": "Koondamine."},
                {"id": "TLS_97", "text": "Etteteatamine."},
            ],
            ["TLS_89"],
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["needs_repair"])
        self.assertIn("deadline", report["missing_answer"])

    def test_multi_fine_requires_deadline_remedy_and_payment_sources(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description=(
                "Sain rahatrahvi. Kaebetähtaeg läks mööda. "
                "Kas tähtaega saab ennistada ja kas trahvi saab ositi maksta?"
            ),
            search_text="rahatrahv kaebetähtaeg ennistada ositi maksta",
            current_intents=["missed_deadline", "challenge_decision", "payment_plan"],
            fine_context=True,
        )
        laws = [
            {"id": "VTMS_118", "text": "Tähtaja ennistamine."},
            {"id": "VTMS_114", "text": "Kaebus."},
            {"id": "KARS_66", "text": "Rahatrahvi tasumine ositi."},
        ]
        report = CoverageVerifier.verify(
            plan,
            laws,
            ["VTMS_118", "VTMS_114", "KARS_66"],
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["covered_count"], 3)

    def test_missing_trusted_source_is_not_repairable_by_model(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="koondamine etteteatamine",
            current_intents=["deadline"],
            fine_context=False,
        )
        report = CoverageVerifier.verify(
            plan,
            [{"id": "TLS_89", "text": "Koondamine."}],
            ["TLS_89"],
        )
        self.assertFalse(report["passed"])
        self.assertFalse(report["needs_repair"])
        self.assertIn("deadline", report["missing_source"])

    def test_source_digest_cites_each_required_source(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="koondamine etteteatamine",
            current_intents=["deadline"],
            fine_context=False,
        )
        laws = [
            {"id": "TLS_89", "text": "Koondamine on töölepingu erakorralise ülesütlemise alus."},
            {"id": "TLS_97", "text": "Tööandja peab töölepingu ülesütlemisest ette teatama."},
        ]
        report = CoverageVerifier.verify(plan, laws, ["TLS_89"])
        digest = CoverageVerifier.build_source_digest(report, laws)
        self.assertIn("[TLS_89]", digest)
        self.assertIn("[TLS_97]", digest)
        self.assertIn("ÕIGUSLIK KOHALDAMINE:", digest)


    def test_repair_instructions_pin_redundancy_claims_to_exact_sources(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="koondamine etteteatamine",
            current_intents=["deadline"],
            fine_context=False,
        )
        laws = [
            {"id": "TLS_89", "text": "Koondamine."},
            {"id": "TLS_97", "text": "Etteteatamine."},
        ]
        report = CoverageVerifier.verify(plan, laws, ["TLS_89"])
        instructions = CoverageVerifier.repair_instructions(report)

        self.assertIn("TÄPSELT 2 elementi", instructions)
        self.assertIn("KOHUSTUS 1/2 [procedure]", instructions)
        self.assertIn("- source_id: TLS_89", instructions)
        self.assertIn("KOHUSTUS 2/2 [deadline]", instructions)
        self.assertIn("- source_id: TLS_97", instructions)
        self.assertIn("Ära kuluta ühtegi claims elementi kõrvalteemale", instructions)

    def test_repair_instructions_pin_multi_fine_to_three_audited_sources(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description=(
                "Sain rahatrahvi. Kaebetähtaeg läks mööda. "
                "Kas tähtaega saab ennistada ja kas trahvi saab ositi maksta?"
            ),
            search_text="rahatrahv kaebetähtaeg ennistada ositi maksta",
            current_intents=["missed_deadline", "challenge_decision", "payment_plan"],
            fine_context=True,
        )
        laws = [
            {"id": "VTMS_118", "text": "Tähtaja ennistamine."},
            {"id": "VTMS_114", "text": "Kaebus."},
            {"id": "KARS_66", "text": "Rahatrahvi tasumine ositi."},
        ]
        report = CoverageVerifier.verify(plan, laws, [])
        instructions = CoverageVerifier.repair_instructions(report)

        self.assertIn("TÄPSELT 3 elementi", instructions)
        self.assertIn("KOHUSTUS 1/3 [deadline]", instructions)
        self.assertIn("- source_id: VTMS_118", instructions)
        self.assertIn("KOHUSTUS 2/3 [remedy]", instructions)
        self.assertIn("- source_id: VTMS_114", instructions)
        self.assertIn("KOHUSTUS 3/3 [payment]", instructions)
        self.assertIn("- source_id: KARS_66", instructions)

    def test_orchestrator_repairs_missing_coverage_without_fallback(self):
        laws = [
            {"id": "TLS_89", "title": "TLS § 89", "text": "Koondamise alus on kirjeldatud sättes.", "source": "RT"},
            {"id": "TLS_97", "title": "TLS § 97", "text": "Etteteatamise tähtaeg on kirjeldatud sättes.", "source": "RT"},
        ]
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="koondamine etteteatamine",
            current_intents=["deadline"],
            fine_context=False,
        )
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
        pipeline = AnalysisPipelineRun()
        for stage in ("case_understanding", "document_evidence", "legal_retrieval"):
            pipeline.complete(stage)
        prepared = SimpleNamespace(
            pipeline=pipeline,
            current_turn="Mind koondatakse. Kui pikk etteteatamine on?",
            answer_requirements=[],
            obligation_plan=plan,
            document_spans=[],
            relevance_text="koondamine etteteatamine",
            route_plan=SimpleNamespace(employment_form_question=False),
            analysis_laws=laws,
        )
        request = SimpleNamespace(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            case_context="",
            event_date="",
        )
        ai = _RepairingOfflineAI()
        executed = asyncio.run(orchestrator.execute(
            request,
            prepared,
            ai_service=ai,
            source_verifier=SourceVerifier(),
        ))
        self.assertEqual(ai.calls, 2)
        self.assertFalse(executed.fallback_used)
        self.assertTrue(executed.coverage_repair_used)
        self.assertTrue(executed.coverage_report["passed"])
        self.assertEqual(set(executed.verified_sources), {"TLS_89", "TLS_97"})


if __name__ == "__main__":
    unittest.main()
