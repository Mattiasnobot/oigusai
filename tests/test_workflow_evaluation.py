import unittest

from scripts.evaluate_workflow import build_report


class WorkflowEvaluationTests(unittest.TestCase):
    def test_report_keeps_safety_and_retrieval_checks_separate(self):
        results = [{
            "id": "A",
            "checks": {
                "intake_summary": True,
                "case_card": True,
                "deadline_safe": True,
                "no_identifier_question": True,
                "retrieval": False,
            },
            "workflow_ok": False,
        }]
        report = build_report(results, 1.25)
        self.assertEqual(report["checks"]["deadline_safe"], 1)
        self.assertEqual(report["checks"]["retrieval"], 0)
        self.assertEqual(report["failures"], ["A"])
        self.assertFalse(report["acceptance_passed"])


if __name__ == "__main__":
    unittest.main()
