import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.evaluate_model import assess_case, build_report, load_suite


class EvaluateModelTests(unittest.TestCase):
    def test_load_suite_rejects_duplicate_ids(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "cases.json"
            path.write_text(json.dumps({
                "cases": [
                    {"id": "A", "query": "üks"},
                    {"id": "A", "query": "kaks"},
                ]
            }), encoding="utf-8")
            with self.assertRaises(Exception):
                load_suite(path)

    def test_assess_case_requires_each_source_group(self):
        case = {
            "expected_source_groups": [["VTMS_118"], ["KARS_66", "VTMS_204"]],
            "required_phrases_all": ["ositi"],
            "required_phrases_any": ["ennist", "läbi vaatamata"],
        }
        finalized = {
            "analysis_text": "Tähtaega võib ennistada ja rahatrahvi võib tasuda ositi.",
            "verified_sources": ["VTMS_118", "KARS_66"],
            "pipeline": {"status": "completed"},
            "verification_status": "EVIDENCE_VERIFIED",
            "is_mock": False,
        }
        result = assess_case(case, finalized, fallback_used=False)
        self.assertTrue(result["safe_pass"])
        self.assertTrue(result["model_pass"])

    def test_fallback_is_safe_but_not_model_pass(self):
        case = {"expected_source_groups": [["TLS_95"]]}
        finalized = {
            "analysis_text": "Kontrollitud fallback [TLS_95].",
            "verified_sources": ["TLS_95"],
            "pipeline": {"status": "completed"},
            "verification_status": "SOURCE_ONLY_FALLBACK",
            "is_mock": False,
        }
        result = assess_case(case, finalized, fallback_used=True)
        self.assertTrue(result["safe_pass"])
        self.assertFalse(result["model_pass"])
        self.assertFalse(result["real_model_used"])

    def test_report_applies_model_and_fallback_thresholds(self):
        suite = {
            "version": "test",
            "acceptance": {
                "max_fallback_rate": 0.25,
                "min_model_pass_rate": 0.75,
            },
        }
        results = [
            {"id": "A", "safe_pass": True, "model_pass": True, "fallback_used": False, "is_mock": False},
            {"id": "B", "safe_pass": True, "model_pass": True, "fallback_used": False, "is_mock": False},
            {"id": "C", "safe_pass": True, "model_pass": True, "fallback_used": False, "is_mock": False},
            {"id": "D", "safe_pass": True, "model_pass": False, "fallback_used": True, "is_mock": False},
        ]
        report = build_report(suite, results, model_name="test-model", duration_seconds=1.0)
        self.assertTrue(report["acceptance_passed"])
        self.assertEqual(report["fallback_rate"], 0.25)
        self.assertEqual(report["model_pass_rate"], 0.75)


if __name__ == "__main__":
    unittest.main()
