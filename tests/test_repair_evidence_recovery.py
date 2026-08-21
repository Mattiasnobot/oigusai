import json
import unittest

from services.offline_ai import OfflineAIService
from verifiers.source_verifier import SourceVerifier


class RepairEvidenceRecoveryTests(unittest.TestCase):
    def test_focused_repair_recovers_only_inexact_evidence_for_supported_claims(self):
        service = OfflineAIService(allow_mock=False)
        laws = [
            {
                "id": "TLS_89",
                "title": "Töölepingu seadus § 89",
                "text": (
                    "Tööandja võib töölepingu erakorraliselt üles öelda, kui "
                    "töösuhte jätkamine muutub võimatuks töömahu vähenemise tõttu "
                    "või töö ümberkorraldamise tõttu (koondamine)."
                ),
                "source": "Riigi Teataja",
            },
            {
                "id": "TLS_97",
                "title": "Töölepingu seadus § 97",
                "text": (
                    "Tööandja peab erakorralisest ülesütlemisest töötajale ette "
                    "teatama seaduses sätestatud etteteatamistähtaega järgides."
                ),
                "source": "Riigi Teataja",
            },
        ]
        raw = json.dumps({
            "claims": [
                {
                    "text": (
                        "Tööandja võib töölepingu erakorraliselt üles öelda "
                        "töömahu vähenemise tõttu."
                    ),
                    "source_id": "TLS_89",
                    "evidence": (
                        "Tööandja võib töölepingu lõpetada töömahu vähenemise tõttu."
                    ),
                },
                {
                    "text": (
                        "Tööandja peab erakorralisest ülesütlemisest töötajale "
                        "ette teatama."
                    ),
                    "source_id": "TLS_97",
                    "evidence": (
                        "Tööandja peab töötajale erakorralisest ülesütlemisest ette teatama."
                    ),
                },
            ]
        }, ensure_ascii=False)

        analysis, claims, diagnostics = service.prepare_structured_repair_response(
            raw,
            laws,
            "Mind koondatakse. Kui pikk etteteatamine on?",
        )
        valid, sources = SourceVerifier().verify_sources(analysis, laws)

        self.assertTrue(valid)
        self.assertEqual(sources, ["TLS_89", "TLS_97"])
        self.assertEqual(len(claims), 2)
        self.assertEqual(diagnostics["raw_source_ids"], ["TLS_89", "TLS_97"])
        self.assertEqual(diagnostics["evidence_recovered_count"], 2)
        self.assertEqual(diagnostics["accepted_source_ids"], ["TLS_89", "TLS_97"])
        self.assertEqual(diagnostics["dropped_claims"], [])
        for claim in claims:
            self.assertEqual(claim["verification_status"], "EVIDENCE_VERIFIED")

    def test_focused_repair_does_not_recover_unknown_or_unsupported_claims(self):
        service = OfflineAIService(allow_mock=False)
        laws = [{
            "id": "TLS_89",
            "title": "Töölepingu seadus § 89",
            "text": "Koondamine võib olla seotud töömahu vähenemisega.",
            "source": "Riigi Teataja",
        }]
        raw = json.dumps({
            "claims": [
                {
                    "text": "Tööandja peab alati maksma kümne kuu hüvitise.",
                    "source_id": "TLS_89",
                    "evidence": "Tööandja peab maksma hüvitise.",
                },
                {
                    "text": "Väide tundmatust allikast.",
                    "source_id": "FAKE_999",
                    "evidence": "Väljamõeldud evidence.",
                },
            ]
        })

        analysis, claims, diagnostics = service.prepare_structured_repair_response(
            raw, laws, "test"
        )

        self.assertEqual(analysis, "")
        self.assertEqual(claims, [])
        self.assertEqual(diagnostics["evidence_recovered_count"], 0)
        reasons = {item["reason"] for item in diagnostics["dropped_claims"]}
        self.assertIn("evidence_not_recoverable", reasons)
        self.assertIn("unknown_source", reasons)
        self.assertEqual(diagnostics["accepted_source_ids"], [])


if __name__ == "__main__":
    unittest.main()
