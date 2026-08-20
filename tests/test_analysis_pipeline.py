import unittest

from services.analysis_pipeline import AnalysisPipelineRun, VerifiedAnswerBuilder


class AnalysisPipelineTests(unittest.TestCase):
    def test_trace_retains_stage_metadata_not_case_text(self):
        run = AnalysisPipelineRun()
        for name in run.ORDER:
            run.complete(name, count=1)
        result = run.public()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["retains_user_text"])
        self.assertEqual(len(result["stages"]), 7)

    def test_layered_answer_uses_verified_claim_and_marks_unknowns(self):
        claim = {
            "kind": "legal",
            "text": "Töölepingu ülesütlemine peab olema kirjalikku taasesitamist võimaldavas vormis.",
            "verification_status": "EVIDENCE_VERIFIED",
            "sources": [{"id": "TLS_95"}],
        }
        result = VerifiedAnswerBuilder().build(
            analysis="SOOVITUSED:\n1. Säilita tööandja sõnumid.",
            claims=[claim],
            verification_status="EVIDENCE_VERIFIED",
            warning="Esmane selgitus.",
            case_card={"missing_facts": ["kas ülesütlemine anti kirjalikult"]},
            urgency={"level": "normal", "questions": []},
        )
        self.assertIn("kirjalikku", result["short_answer"])
        self.assertEqual(result["why"][0]["source_ids"], ["TLS_95"])
        self.assertIn("kas ülesütlemine", result["unknowns"][0])
        self.assertEqual(result["confidence"], "conditional")

    def test_fallback_is_never_presented_as_supported(self):
        result = VerifiedAnswerBuilder().build(
            analysis="Kontrollitud allikate kokkuvõte.",
            claims=[],
            verification_status="SOURCE_ONLY_FALLBACK",
            warning="",
            fallback_used=True,
        )
        self.assertEqual(result["confidence"], "limited")


if __name__ == "__main__":
    unittest.main()
