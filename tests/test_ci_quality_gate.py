import json
import tempfile
import unittest
from pathlib import Path

from scripts.ci_quality_gate import QualityGateError, validate_evaluation_assets, validate_laws


class CIQualityGateTests(unittest.TestCase):
    def test_validate_laws_accepts_unique_required_records(self):
        report = validate_laws([
            {"id": "A_1", "title": "A § 1", "text": "Esimene.", "source": "RT", "content_hash": "a"},
            {"id": "B_2", "title": "B § 2", "text": "Teine.", "source": "RT", "content_hash": "b"},
        ])
        self.assertEqual(report["legal_sections"], 2)
        self.assertEqual(report["unique_ids"], 2)
        self.assertEqual(len(report["corpus_fingerprint"]), 64)

    def test_validate_laws_rejects_duplicate_ids(self):
        with self.assertRaises(QualityGateError):
            validate_laws([
                {"id": "A_1", "title": "A § 1", "text": "Üks.", "source": "RT"},
                {"id": "a_1", "title": "A § 1", "text": "Kaks.", "source": "RT"},
            ])

    def test_evaluation_assets_must_match_committed_case_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "eval").mkdir()
            (root / "eval/query_cases.json").write_text(
                json.dumps([{"id": "CASE-1"}], ensure_ascii=False), encoding="utf-8"
            )
            (root / "eval/V91_WORKFLOW_BASELINE_2026-08-11.json").write_text(
                json.dumps({
                    "cases": 2,
                    "acceptance_passed": True,
                    "retrieval_baseline_required": 1,
                }),
                encoding="utf-8",
            )
            with self.assertRaises(QualityGateError):
                validate_evaluation_assets(root)


if __name__ == "__main__":
    unittest.main()
