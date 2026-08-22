import base64
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

import main


AUTH_HEADERS = (
    {main.ACCESS_CODE_HEADER: main.settings.app_access_code}
    if main.settings.app_access_code else {}
)


class V91ApiTests(unittest.TestCase):
    def test_intake_creates_memory_only_matter_and_versioned_case_card(self):
        intake = Mock()
        intake.understand.return_value = {
            "input_type": "story",
            "topic": "koondamine",
            "summary": "Tööandja teatas koondamisest.",
            "user_goal": "Soovin teada järgmisi samme.",
            "help_types": ["next_steps"],
            "parties": [{"role": "tööandja", "label": "tööandja", "evidence": "tööandja teatas"}],
            "events": [], "amounts": [], "documents": [],
            "missing_facts": ["teate kuupäev"],
            "clarification_questions": [], "ready_for_analysis": True,
            "search_query": "koondamine", "analysis_context": "koondamine",
            "input_length": 31, "used_ai": False,
        }
        with TestClient(main.app) as client:
            main.app.state.intake_service = intake
            response = client.post("/intake", headers=AUTH_HEADERS, json={
                "case_description": "Tööandja teatas koondamisest."
            })
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["matter_id"])
            self.assertEqual(data["case_card"]["revision"], 1)
            matter = client.get(
                f"/matters/{data['matter_id']}", headers=AUTH_HEADERS
            ).json()
            self.assertEqual(matter["case_card"]["summary"], data["case_card"]["summary"])

    def test_case_card_can_be_revised_and_safe_draft_created(self):
        with TestClient(main.app) as client:
            created = client.post("/matters", headers=AUTH_HEADERS, json={
                "title": "Tööasi"
            }).json()
            matter_id = created["matter_id"]
            patched = client.patch(
                f"/matters/{matter_id}/case-card",
                headers=AUTH_HEADERS,
                json={
                    "expected_revision": 0,
                    "changes": {
                        "summary": "Tööandja ütles lepingu üles.",
                        "user_goal": "Soovin kirjalikku selgitust.",
                    },
                },
            )
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(patched.json()["case_card"]["revision"], 1)
            draft = client.post(
                f"/matters/{matter_id}/drafts",
                headers=AUTH_HEADERS,
                json={"draft_type": "selgitustaotlus"},
            )
            self.assertEqual(draft.status_code, 200)
            self.assertIn("Tööandja ütles lepingu üles", draft.json()["body"])
            self.assertTrue(draft.json()["placeholders_present"])

    def test_document_upload_contains_exact_span_insights(self):
        payload = base64.b64encode(
            "Otsus 10.08.2026. Tasuda tuleb 250 eurot.".encode()
        ).decode("ascii")
        with TestClient(main.app) as client:
            response = client.post("/documents", headers=AUTH_HEADERS, json={
                "file_name": "otsus.txt", "content_base64": payload
            })
        self.assertEqual(response.status_code, 200)
        insights = response.json()["document"]["insights"]
        self.assertEqual(insights["dates"][0]["value"], "10.08.2026")
        self.assertEqual(insights["amounts"][0]["value"], "250 eurot")

    def test_admin_shell_is_public_but_metrics_require_access(self):
        with TestClient(main.app) as client:
            page = client.get("/admin")
            metrics = client.get("/admin/metrics", headers=AUTH_HEADERS)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Kvaliteedipaneel", page.text)
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["version"], "0.9.1")
        self.assertFalse(metrics.json()["retains_user_text"])
        self.assertIn("verified_live_context", metrics.json())


if __name__ == "__main__":
    unittest.main()
