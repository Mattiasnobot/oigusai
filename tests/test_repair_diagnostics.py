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


    def test_quantity_gate_canonicalizes_estonian_number_case_variants(self):
        service = OfflineAIService(allow_mock=False, repair_debug=True)
        claim = (
            "Kümne ja enam aastat kestnud töösuhte korral tuleb ette teatada "
            "vähemalt 90 kalendripäeva."
        )
        evidence = (
            "Kümme ja enam tööaastat kestnud töösuhte korral tuleb ette teatada "
            "vähemalt 90 kalendripäeva."
        )

        debug = service._claim_support_debug(claim, evidence)

        self.assertTrue(service._claim_is_supported_by_evidence(claim, evidence))
        self.assertTrue(debug["passed"])
        self.assertEqual(debug["missing_quantities"], [])
        self.assertIn("kümme", debug["claim_quantities"])
        self.assertNotIn("kümne", debug["claim_quantities"])

    def test_quantity_gate_still_rejects_different_deadline(self):
        service = OfflineAIService(allow_mock=False, repair_debug=True)
        claim = "Kümne ja enam tööaasta korral tuleb ette teatada 90 kalendripäeva."
        evidence = "Kümme ja enam tööaasta korral tuleb ette teatada 60 kalendripäeva."

        debug = service._claim_support_debug(claim, evidence)

        self.assertFalse(service._claim_is_supported_by_evidence(claim, evidence))
        self.assertFalse(debug["passed"])
        self.assertEqual(debug["missing_quantities"], ["90"])

    def test_quantity_gate_keeps_word_numbers_separate_from_digits(self):
        service = OfflineAIService(allow_mock=False, repair_debug=True)
        claim = "Tööandja peab ette teatama vähemalt üks kuu."
        evidence = "1 (1) Tööandja peab ette teatama vähemalt kuu."

        debug = service._claim_support_debug(claim, evidence)

        self.assertFalse(service._claim_is_supported_by_evidence(claim, evidence))
        self.assertEqual(debug["missing_quantities"], ["üks"])
        self.assertIn("1", debug["evidence_quantities"])


if __name__ == "__main__":
    unittest.main()
