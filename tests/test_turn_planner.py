import unittest

from services.turn_planner import ConversationTurnPlanner


class ConversationTurnPlannerTests(unittest.TestCase):
    def test_latest_turn_keeps_every_requested_outcome(self):
        intents = ConversationTurnPlanner.detect_intents(
            "Kui kaebe tähtaeg on möödas, kas saan veel kaevata või pean maksma? "
            "Soovin 4000 eurot tasuda järelmaksuga."
        )

        self.assertIn("missed_deadline", intents)
        self.assertIn("challenge_decision", intents)
        self.assertIn("payment_plan", intents)

    def test_decision_asks_one_question_for_legally_decisive_missing_facts(self):
        history = (
            "Abipolitsei tegi mulle trahvi. Sain trahviteate, kuid ei tea selle "
            "täpset liiki."
        )
        current = (
            "Kaebe tähtaeg on möödas. Kas seda saab veel vaidlustada ja kas "
            "4000 eurot saab maksta osade kaupa?"
        )

        decision = ConversationTurnPlanner.decide(current, history)

        self.assertEqual(decision.next_action, "clarify")
        self.assertIn("dokumendi täpne pealkiri", decision.clarification_question)
        self.assertIn("millal said selle kätte", decision.clarification_question)
        self.assertIn("kohtutäituri", decision.clarification_question)
        self.assertIn("4000 eurot", decision.clarification_question)
        self.assertNotIn("trahviteade", decision.search_query.casefold())
        self.assertIn("tähtaja ennistamise", decision.search_query)
        self.assertIn("tasumine ositi", decision.search_query)

    def test_answered_planner_question_does_not_loop(self):
        question = (
            "Palun vaata dokumendilt, mis on dokumendi täpne pealkiri, millal "
            "said selle kätte, kas see on juba kohtutäituri või muu täitja käes?"
        )
        history = (
            "Sain rahatrahvi. Kaebetähtaeg on möödas ja soovin maksta osade kaupa.\n"
            f"ÕigusAI küsimus: {question}\n"
            "Kasutaja vastus: Ma ei tea."
        )

        decision = ConversationTurnPlanner.decide(
            "Ma ei tea.",
            history,
            existing_help_types=["rights_explanation"],
        )

        self.assertEqual(decision.next_action, "analyze")
        self.assertEqual(decision.clarification_question, "")
        self.assertIn("missed_deadline", decision.current_intents)
        self.assertIn("challenge_decision", decision.current_intents)
        self.assertIn("payment_plan", decision.current_intents)
        self.assertIn("tähtaja ennistamise", decision.search_query)
        self.assertIn("tasumine ositi", decision.search_query)


if __name__ == "__main__":
    unittest.main()
