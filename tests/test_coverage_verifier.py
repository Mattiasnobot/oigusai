import asyncio
import json
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
        self.source_verifier = SourceVerifier()
        self.repair_prompt = ""
        self.repair_schema = {}

    def analyze_case_structured(self, *_args, **_kwargs):
        self.calls += 1
        return (
            "ÕIGUSLIK KOHALDAMINE:\nKoondamise alus on kirjeldatud sättes [TLS_89].\n\n"
            "SOOVITUSED:\nSäilita dokumendid.\n\nKASUTATUD ALLIKAD: [TLS_89]",
            False,
            [],
        )

    def generate_structured(self, prompt, response_schema):
        self.calls += 1
        self.repair_prompt = prompt
        self.repair_schema = response_schema
        return json.dumps({
            "claims": [
                {
                    "text": "Koondamise alus on kirjeldatud sättes.",
                    "source_id": "TLS_89",
                    "evidence": "Koondamise alus on kirjeldatud sättes.",
                },
                {
                    "text": "Etteteatamise tähtaeg on kirjeldatud sättes.",
                    "source_id": "TLS_97",
                    "evidence": "Etteteatamise tähtaeg on kirjeldatud sättes.",
                },
            ]
        }, ensure_ascii=False)

    def claims_from_verified_analysis(self, *_args, **_kwargs):
        return []

    def build_source_only_fallback(self, *_args, **_kwargs):
        raise AssertionError("Coverage repair should succeed before fallback")


class _FormRepairingOfflineAI(OfflineAIService):
    def __init__(self):
        self.calls = 0
        self.source_verifier = SourceVerifier()
        self.repair_schema = {}

    def analyze_case_structured(self, *_args, **_kwargs):
        self.calls += 1
        return (
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas vormis [TLS_95].\n\n"
            "SOOVITUSED:\nSäilita dokumendid.\n\nKASUTATUD ALLIKAD: [TLS_95]",
            False,
            [],
        )

    def generate_structured(self, _prompt, response_schema):
        self.calls += 1
        self.repair_schema = response_schema
        return json.dumps({
            "claims": [{
                "text": (
                    "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas "
                    "vormis ja vorminõuet rikkudes tehtud ülesütlemisavaldus on tühine."
                ),
                "source_id": "TLS_95",
                "evidence": (
                    "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas "
                    "vormis. Vorminõuet rikkudes tehtud ülesütlemisavaldus on tühine."
                ),
            }]
        }, ensure_ascii=False)

    def claims_from_verified_analysis(self, *_args, **_kwargs):
        return []

    def build_source_only_fallback(self, *_args, **_kwargs):
        raise AssertionError("Focused form repair should succeed before fallback")


class _RelevanceRepairingOfflineAI(OfflineAIService):
    def __init__(self):
        self.calls = 0
        self.source_verifier = SourceVerifier()

    def analyze_case_structured(self, *_args, **_kwargs):
        self.calls += 1
        return (
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Koondamise alus on kirjeldatud sättes [TLS_89].\n"
            "Etteteatamise tähtaeg on kirjeldatud sättes [TLS_97].\n\n"
            "SOOVITUSED:\nSäilita dokumendid.\n\n"
            "KASUTATUD ALLIKAD: [TLS_89] [TLS_97]",
            False,
            [],
        )

    def generate_structured(self, _prompt, _response_schema):
        self.calls += 1
        return json.dumps({
            "claims": [
                {
                    "text": "Koondamise alus on kirjeldatud sättes.",
                    "source_id": "TLS_89",
                    "evidence": "Koondamise alus on kirjeldatud sättes.",
                },
                {
                    "text": "Etteteatamise tähtaeg on kirjeldatud sättes.",
                    "source_id": "TLS_97",
                    "evidence": "Etteteatamise tähtaeg on kirjeldatud sättes.",
                },
            ]
        }, ensure_ascii=False)

    def claims_from_verified_analysis(self, *_args, **_kwargs):
        return []

    def build_source_only_fallback(self, *_args, **_kwargs):
        raise AssertionError("Semantic relevance repair should succeed before fallback")


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

    def test_form_answer_terms_trigger_repair_even_when_tls95_is_cited(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Kas tööandja võib töölepingu ainult suuliselt üles öelda?",
            search_text="töölepingu suuline ülesütlemine",
            current_intents=[],
            fine_context=False,
        )
        laws = [{
            "id": "TLS_95",
            "text": (
                "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas vormis. "
                "Vorminõuet rikkudes tehtud ülesütlemisavaldus on tühine."
            ),
        }]
        report = CoverageVerifier.verify(
            plan,
            laws,
            ["TLS_95"],
            answer_text=(
                "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist "
                "võimaldavas vormis [TLS_95]."
            ),
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["needs_repair"])
        self.assertIn("form_requirement", report["missing_answer"])
        self.assertEqual(
            report["obligations"][0]["missing_answer_terms"],
            [["tühine"]],
        )

    def test_fine_challenge_requires_challenge_language_when_answer_is_checked(self):
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
            {"id": "VTMS_114", "text": "Maakohtule kaebuse esitamise õigus."},
            {"id": "KARS_66", "text": "Rahatrahvi tasumine ositi."},
        ]
        report = CoverageVerifier.verify(
            plan,
            laws,
            ["VTMS_118", "KARS_66"],
            answer_text="Tähtaega võib ennistada ja rahatrahvi võib tasuda ositi.",
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["needs_repair"])
        self.assertIn("remedy", report["missing_answer"])

    def test_repair_schema_pins_exact_claim_count_and_allowed_ids(self):
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
        schema = CoverageVerifier.repair_schema(report)
        claims = schema["properties"]["claims"]
        self.assertEqual(claims["minItems"], 2)
        self.assertEqual(claims["maxItems"], 2)
        self.assertEqual(
            claims["items"]["properties"]["source_id"]["enum"],
            ["TLS_89", "TLS_97"],
        )

    def test_repair_laws_keep_only_audited_coverage_sources(self):
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Mind koondatakse. Kui pikk etteteatamine on?",
            search_text="koondamine etteteatamine",
            current_intents=["deadline"],
            fine_context=False,
        )
        laws = [
            {"id": "TLS_89", "text": "Koondamine."},
            {"id": "TLS_97", "text": "Etteteatamine."},
            {"id": "TLS_100", "text": "Kõrvaline säte."},
        ]
        report = CoverageVerifier.verify(plan, laws, ["TLS_89"])
        repair_laws = CoverageVerifier.repair_laws(report, laws)
        self.assertEqual(
            [law["id"] for law in repair_laws],
            ["TLS_89", "TLS_97"],
        )

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
            {"id": "TLS_100", "title": "TLS § 100", "text": "Kõrvaline säte.", "source": "RT"},
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
        self.assertEqual(
            ai.repair_schema["properties"]["claims"]["minItems"], 2
        )
        self.assertNotIn("TLS_100", ai.repair_prompt)
        self.assertEqual(
            executed.coverage_repair_diagnostics["target_sources"],
            ["TLS_89", "TLS_97"],
        )
        self.assertTrue(executed.coverage_repair_diagnostics["accepted"])
        self.assertFalse(executed.fallback_used)
        self.assertTrue(executed.coverage_repair_used)
        self.assertTrue(executed.coverage_report["passed"])
        self.assertEqual(set(executed.verified_sources), {"TLS_89", "TLS_97"})


    def test_semantic_relevance_can_trigger_the_single_focused_repair(self):
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
        relevance.verify_answer.side_effect = [
            SimpleNamespace(relevant=False, missing_concepts=["forced"], clarification=""),
            SimpleNamespace(relevant=True, missing_concepts=[], clarification=""),
            SimpleNamespace(relevant=True, missing_concepts=[], clarification=""),
        ]
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
        ai = _RelevanceRepairingOfflineAI()
        executed = asyncio.run(orchestrator.execute(
            request, prepared, ai_service=ai, source_verifier=SourceVerifier()
        ))
        self.assertEqual(ai.calls, 2)
        self.assertFalse(executed.fallback_used)
        self.assertTrue(executed.coverage_repair_used)
        self.assertEqual(
            executed.coverage_repair_diagnostics["trigger"],
            "semantic_relevance",
        )
        self.assertTrue(executed.coverage_repair_diagnostics["accepted"])

    def test_orchestrator_repairs_form_semantics_before_source_fallback(self):
        laws = [{
            "id": "TLS_95",
            "title": "TLS § 95",
            "text": (
                "Ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas vormis. "
                "Vorminõuet rikkudes tehtud ülesütlemisavaldus on tühine."
            ),
            "source": "RT",
        }]
        plan = MultiIssueRetrievalPlanner.plan(
            case_description="Kas tööandja võib töölepingu ainult suuliselt üles öelda?",
            search_text="töölepingu suuline ülesütlemine",
            current_intents=[],
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
            current_turn="Kas tööandja võib töölepingu ainult suuliselt üles öelda?",
            answer_requirements=[],
            obligation_plan=plan,
            document_spans=[],
            relevance_text="töölepingu suuline ülesütlemine",
            route_plan=SimpleNamespace(employment_form_question=True),
            analysis_laws=laws,
        )
        request = SimpleNamespace(
            case_description="Kas tööandja võib töölepingu ainult suuliselt üles öelda?",
            case_context="",
            event_date="",
        )
        ai = _FormRepairingOfflineAI()
        executed = asyncio.run(orchestrator.execute(
            request,
            prepared,
            ai_service=ai,
            source_verifier=SourceVerifier(),
        ))
        self.assertEqual(ai.calls, 2)
        self.assertEqual(
            ai.repair_schema["properties"]["claims"]["maxItems"], 1
        )
        self.assertFalse(executed.fallback_used)
        self.assertTrue(executed.coverage_repair_used)
        self.assertTrue(executed.coverage_report["passed"])
        self.assertIn("tühine", executed.analysis_text.casefold())


if __name__ == "__main__":
    unittest.main()
