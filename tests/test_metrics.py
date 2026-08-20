import unittest

from services.metrics import QualityMetricsStore


class QualityMetricsTests(unittest.TestCase):
    def test_metrics_are_aggregate_only(self):
        store = QualityMetricsStore()
        store.record_request("analyze", 200)
        store.record_analysis(
            duration_ms=1250,
            verification_status="EVIDENCE_VERIFIED",
            fallback=False,
            claim_count=3,
            source_count=2,
        )
        data = store.snapshot()
        self.assertEqual(data["analyses"], 1)
        self.assertEqual(data["latency_ms"]["p95"], 1250)
        self.assertEqual(data["privacy"], "aggregate_only_no_user_text")
        self.assertNotIn("query", data)


if __name__ == "__main__":
    unittest.main()
