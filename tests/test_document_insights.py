import unittest

from services.document_insights import DocumentInsightService, SafeDraftService
from services.documents import LocalDocumentService


class DocumentInsightTests(unittest.TestCase):
    def test_document_facts_keep_exact_source_coordinates(self):
        document = LocalDocumentService("http://localhost:11434").process(
            "otsus.txt",
            "Otsus on tehtud 10.08.2026. Tasuda tuleb 4000 eurot. Otsuse võib vaidlustada.".encode(),
        )
        insights = document["insights"]
        self.assertEqual(insights["dates"][0]["value"], "10.08.2026")
        self.assertEqual(insights["amounts"][0]["value"], "4000 eurot")
        source = insights["amounts"][0]["source"]
        self.assertEqual(source["document_id"], document["document_id"])
        self.assertEqual(source["page"], 1)
        self.assertIn(source["evidence"], document["spans"][0]["text"])

    def test_safe_draft_uses_card_facts_and_visible_placeholders(self):
        result = SafeDraftService().build(
            "vaie",
            {
                "summary": "Sain 10.08.2026 otsuse.",
                "user_goal": "Palun otsus uuesti läbi vaadata.",
                "events": [],
            },
            [{"file_name": "otsus.pdf"}],
        )
        self.assertIn("Sain 10.08.2026 otsuse.", result["body"])
        self.assertIn("[TÄIDA SAAJA NIMI JA AADRESS]", result["body"])
        self.assertTrue(result["placeholders_present"])

    def test_safe_draft_turns_ui_goal_into_first_person_request(self):
        result = SafeDraftService().build(
            "selgitustaotlus",
            {
                "summary": "Tööandja teatas ülesütlemisest suuliselt.",
                "user_goal": "Soovib olukorra ja oma võimaluste selgitust.",
                "events": [],
            },
            [],
        )

        self.assertIn("Soovin olukorra ja oma võimaluste selgitust.", result["body"])
        self.assertNotIn("\nSoovib olukorra", result["body"])


if __name__ == "__main__":
    unittest.main()
