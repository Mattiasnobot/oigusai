import unittest

from services.retrieval_policy import RetrievalPolicy


class RetrievalPolicyTests(unittest.TestCase):
    def test_generic_trahviteade_does_not_force_warning_notice_route(self):
        plan = RetrievalPolicy.plan(
            case_description="Abipolitsei trahvis mind ja sain trahviteate.",
            search_text="Abipolitsei trahvis mind. Sain trahviteate.",
            current_intents=(),
            fine_context=True,
        )

        self.assertNotIn("VTMS_54B2", plan.routed_ids)
        self.assertNotIn("VTMS_54B5", plan.routed_ids)

    def test_explicit_warning_notice_uses_warning_notice_sections(self):
        plan = RetrievalPolicy.plan(
            case_description=(
                "Dokument ütleb, et see on mootorsõiduki eest vastutava isiku "
                "hoiatustrahvi trahviteade."
            ),
            search_text="hoiatustrahvi trahviteade",
            current_intents=(),
            fine_context=True,
        )

        self.assertEqual(
            plan.document_route_ids,
            ("ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_54B2", "VTMS_54B5"),
        )

    def test_short_procedure_decision_uses_its_own_sections(self):
        plan = RetrievalPolicy.plan(
            case_description="Sain lühimenetluse otsuse.",
            search_text="lühimenetluse otsus mõjutustrahv",
            current_intents=("challenge_decision",),
            fine_context=True,
        )

        self.assertEqual(
            plan.document_route_ids,
            ("ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_54B9", "VTMS_54B11"),
        )

    def test_missed_deadline_and_payment_plan_are_both_preserved(self):
        plan = RetrievalPolicy.plan(
            case_description="Sain rahatrahvi.",
            search_text="rahatrahv väärteomenetlus",
            current_intents=("missed_deadline", "challenge_decision", "payment_plan"),
            fine_context=True,
        )

        self.assertEqual(
            plan.intent_route_ids,
            ("VTMS_114", "VTMS_118", "KARS_66", "VTMS_57", "VTMS_74", "VTMS_204"),
        )

    def test_oral_employment_termination_marks_form_coverage(self):
        plan = RetrievalPolicy.plan(
            case_description="Kas tööandja võib töölepingu suuliselt üles öelda?",
            search_text="töölepingu ülesütlemine suuline",
            current_intents=("rights_explanation",),
            fine_context=False,
        )

        self.assertTrue(plan.employment_context)
        self.assertTrue(plan.employment_form_question)
        self.assertEqual(plan.intent_route_ids, ("TLS_88", "TLS_95", "TLS_104"))

    def test_redundancy_termination_uses_redundancy_sections(self):
        plan = RetrievalPolicy.plan(
            case_description="Mind koondati.",
            search_text="koondamine tööleping",
            current_intents=("rights_explanation",),
            fine_context=False,
        )

        self.assertTrue(plan.employment_context)
        self.assertFalse(plan.employment_form_question)
        self.assertEqual(plan.intent_route_ids, ("TLS_89", "TLS_97"))


if __name__ == "__main__":
    unittest.main()
