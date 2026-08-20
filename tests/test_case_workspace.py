import unittest
from datetime import date

from services.case_workspace import CaseCardBuilder, UrgencyAnalyzer


class CaseCardBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = CaseCardBuilder()
        self.intake = {
            "topic": "töölepingu ülesütlemine",
            "summary": "Tööandja ütles lepingu suuliselt üles.",
            "user_goal": "Soovin teada, mida teha.",
            "help_types": ["rights_explanation"],
            "parties": [{"role": "töötaja", "label": "mina", "evidence": "mind vallandati"}],
            "events": [{"date": "10.08.2026", "actor": "tööandja", "action": "ütles lepingu üles", "evidence": "ütles, et olen vallandatud"}],
            "amounts": [],
            "documents": [],
            "missing_facts": ["kas anti kirjalik avaldus"],
        }

    def test_card_retains_exact_intake_evidence_and_revision(self):
        card = self.builder.from_intake(self.intake)
        self.assertEqual(card["revision"], 1)
        self.assertEqual(card["events"][0]["evidence"], "ütles, et olen vallandatud")
        self.assertEqual(card["source"], "user_intake")

    def test_user_revision_is_version_checked(self):
        card = self.builder.from_intake(self.intake)
        revised = self.builder.revise(card, {
            "summary": "Tööandja ei öelnud lepingut veel üles.",
            "missing_facts": [],
        }, expected_revision=1)
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["source"], "user_confirmed")
        with self.assertRaises(ValueError):
            self.builder.revise(revised, {"summary": "vana muudatus"}, expected_revision=1)

    def test_confirmed_correction_survives_later_intake_turn(self):
        original = self.builder.from_intake(self.intake)
        revised = self.builder.revise(
            original,
            {"summary": "Tööandja ei öelnud lepingut üles."},
            expected_revision=1,
        )
        later = self.builder.from_intake(
            {**self.intake, "summary": "Automaatne vana kokkuvõte."},
            previous=revised,
        )
        self.assertEqual(later["summary"], "Tööandja ei öelnud lepingut üles.")
        self.assertEqual(later["source"], "user_confirmed")


class UrgencyAnalyzerTests(unittest.TestCase):
    def test_observed_date_is_not_promoted_to_legal_deadline(self):
        result = UrgencyAnalyzer().analyze(
            "Otsuse vaidlustamise tähtaeg võib olla 15.08.2026.",
            today=date(2026, 8, 11),
        )
        self.assertEqual(result["observed_dates"][0]["days_from_today"], 4)
        self.assertEqual(
            result["observed_dates"][0]["status"],
            "observed_not_legal_deadline",
        )
        self.assertFalse(result["legal_deadline_confirmed"])
        self.assertIn("Millal said otsuse", result["questions"][0])

    def test_immediate_safety_language_is_highest_level(self):
        result = UrgencyAnalyzer().analyze("Ta ähvardab mind vägivallaga.")
        self.assertEqual(result["level"], "immediate")


if __name__ == "__main__":
    unittest.main()
