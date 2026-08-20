import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.analysis_orchestrator import AnalysisOrchestrator
from services.retrieval_planner import MultiIssueRetrievalPlanner


async def immediate_work(_label, func, *args):
    return func(*args)


def interpretation():
    return SimpleNamespace(to_dict=lambda: {
        "expanded_tokens": [],
        "domain_hints": [],
        "section_hints": [],
        "matches": [],
        "notes": [],
    })


class MultiIssueRetrievalPlannerTests(unittest.TestCase):
    def test_fine_question_is_decomposed_into_deadline_remedy_and_payment(self):
        query = (
            "Sain rahatrahvi. Kaebetähtaeg läks mööda. Kas tähtaega saab ennistada "
            "ja kas trahvi saab ositi maksta?"
        )
        plan = MultiIssueRetrievalPlanner.plan(
            case_description=query,
            search_text=query,
            current_intents=["missed_deadline", "challenge_decision", "payment_plan"],
            fine_context=True,
        )

        self.assertTrue(plan.multi_issue)
        self.assertEqual(
            [item.kind for item in plan.obligations],
            ["deadline", "remedy", "payment"],
        )
        self.assertIn("ennistamine", plan.obligations[0].query)
        self.assertIn("maakohtule", plan.obligations[1].query)
        self.assertIn("ositi", plan.obligations[2].query)

    def test_redundancy_question_separates_basis_and_notice_period(self):
        query = (
            "Mind koondatakse. Millistel alustel võib tööandja koondada ja kui pikk "
            "etteteatamine on?"
        )
        plan = MultiIssueRetrievalPlanner.plan(
            case_description=query,
            search_text=query,
            current_intents=["deadline"],
        )

        self.assertTrue(plan.multi_issue)
        self.assertEqual(
            [item.kind for item in plan.obligations],
            ["procedure", "deadline"],
        )
        self.assertIn("koondamise tõttu", plan.obligations[0].query)
        self.assertIn("etteteatamise tähtaeg", plan.obligations[1].query)

    def test_employment_form_question_creates_single_form_obligation(self):
        query = "Kas tööandja võib töölepingu ainult suuliselt üles öelda?"
        plan = MultiIssueRetrievalPlanner.plan(
            case_description=query,
            search_text=query,
            current_intents=["rights_explanation"],
        )

        self.assertFalse(plan.multi_issue)
        self.assertEqual([item.kind for item in plan.obligations], ["form_requirement"])
        self.assertIn("kirjalikku taasesitamist", plan.obligations[0].query)

    def test_prepare_runs_each_multi_issue_query_and_execute_exposes_requirements(self):
        base_law = {
            "id": "VTMS_114",
            "title": "VTMS § 114",
            "text": "Otsuse peale võib esitada maakohtule kaebuse.",
            "source": "RT",
        }
        deadline_law = {
            "id": "VTMS_118",
            "title": "VTMS § 118",
            "text": "Tähtaja möödumisel võib tähtaja ennistamise taotlus olla vajalik.",
            "source": "RT",
        }
        payment_law = {
            "id": "KARS_66",
            "title": "KarS § 66",
            "text": "Rahatrahvi võib mõjuvatel põhjustel tasuda ositi.",
            "source": "RT",
        }
        enforcement_law = {
            "id": "VTMS_204",
            "title": "VTMS § 204",
            "text": "Tähtajaks tasumata rahatrahv saadetakse täitmiseks.",
            "source": "RT",
        }

        def search(query, _event_date):
            normalized = query.casefold()
            if "ennistamine" in normalized:
                return [deadline_law], interpretation()
            if "maakohtule" in normalized:
                return [base_law], interpretation()
            if "ositi" in normalized:
                return [payment_law, enforcement_law], interpretation()
            return [base_law], interpretation()

        legal_service = Mock()
        legal_service.max_results = 5
        legal_service.search_laws_with_context.side_effect = search
        relevance = Mock()
        relevance.verify_laws.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        relevance.verify_answer.return_value = SimpleNamespace(
            relevant=True,
            missing_concepts=[],
            clarification="",
        )
        query = (
            "Sain rahatrahvi. Kaebetähtaeg läks mööda. Kas tähtaega saab ennistada "
            "ja kas trahvi saab ositi maksta?"
        )
        request = SimpleNamespace(
            case_description=query,
            current_message="",
            answer_requirements=[],
            matter_id=None,
            document_ids=[],
            search_query=None,
            event_date=None,
            case_context=None,
        )
        orchestrator = AnalysisOrchestrator(
            legal_service=legal_service,
            matter_store=None,
            relevance_verifier=relevance,
            run_guarded_work=immediate_work,
        )

        prepared = asyncio.run(orchestrator.prepare(request))

        self.assertTrue(prepared.obligation_plan.multi_issue)
        self.assertEqual(legal_service.search_laws_with_context.call_count, 4)
        self.assertEqual(
            {law["id"] for law in prepared.analysis_laws},
            {"VTMS_114", "VTMS_118", "KARS_66", "VTMS_204"},
        )
        self.assertIn("challenge_decision", prepared.current_intents)
        self.assertIn("payment_plan", prepared.current_intents)

        ai_service = Mock()
        ai_service.analyze_case.return_value = (
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Otsuse peale võib esitada kaebuse [VTMS_114]. "
            "Tähtaja ennistamist tuleb eraldi hinnata [VTMS_118]. "
            "Rahatrahvi võib tasuda ositi [KARS_66].",
            False,
        )
        source_verifier = Mock()
        source_verifier.verify_sources.return_value = (
            True,
            ["VTMS_114", "VTMS_118", "KARS_66"],
        )

        asyncio.run(orchestrator.execute(
            request,
            prepared,
            ai_service=ai_service,
            source_verifier=source_verifier,
        ))

        model_case = ai_service.analyze_case.call_args.args[0]
        self.assertIn("VASTUS PEAB KÄSITLEMA", model_case)
        self.assertIn("möödunud tähtaega saab ennistada", model_case)
        self.assertIn("õiguskaitsevahend", model_case)
        self.assertIn("rahatrahvi saab tasuda ositi", model_case)


if __name__ == "__main__":
    unittest.main()
