import json
import unittest

from services.offline_ai import OfflineAIService


class RepairDiagnosticsTests(unittest.TestCase):
    def _law(self):
        return {
            "id": "TLS_97",
            "title": "Töölepingu seadus § 97",
            "text": (
                "Tööandja võib töölepingu erakorraliselt üles öelda "
                "etteteatamistähtaegu järgides. "
                "Erakorralisest ülesütlemisest peab tööandja töötajale ette teatama, "
                "kui töösuhe on kestnud alla ühe tööaasta – vähemalt 15 kalendripäeva."
            ),
            "source": "Riigi Teataja",
        }

    def test_normal_runtime_omits_verbose_repair_claim_diagnostics(self):
        service = OfflineAIService(allow_mock=False)
        law = self._law()
        raw = json.dumps({
            "claims": [{
                "text": "Tööandja peab ette teatama vähemalt 90 kalendripäeva.",
                "source_id": "TLS_97",
                "evidence": "Tööandja peab 90 kalendripäeva ette teatama.",
            }]
        }, ensure_ascii=False)

        _analysis, _claims, diagnostics = service.prepare_structured_repair_response(
            raw, [law], "test"
        )

        self.assertNotIn("claim_diagnostics", diagnostics)

    def test_eval_debug_explains_rejected_repair_candidates(self):
        service = OfflineAIService(allow_mock=False, repair_debug=True)
        law = self._law()
        raw = json.dumps({
            "claims": [{
                "text": "Tööandja peab ette teatama vähemalt 90 kalendripäeva.",
                "source_id": "TLS_97",
                "evidence": "Tööandja peab 90 kalendripäeva ette teatama.",
            }]
        }, ensure_ascii=False)

        analysis, claims, diagnostics = service.prepare_structured_repair_response(
            raw, [law], "test"
        )

        self.assertEqual(analysis, "")
        self.assertEqual(claims, [])
        self.assertEqual(
            diagnostics["dropped_claims"][0]["reason"],
            "evidence_not_recoverable",
        )
        rows = diagnostics["claim_diagnostics"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_id"], "TLS_97")
        self.assertEqual(row["outcome"], "evidence_not_recoverable")
        self.assertFalse(row["model_evidence_is_exact"])
        self.assertTrue(row["candidate_attempts"])
        self.assertTrue(
            all(not item["support_passed"] for item in row["candidate_attempts"])
        )
        self.assertTrue(any(
            "90" in item["support"]["missing_quantities"]
            for item in row["candidate_attempts"]
        ))


if __name__ == "__main__":
    unittest.main()
