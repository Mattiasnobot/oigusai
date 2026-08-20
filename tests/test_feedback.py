import unittest

from services.feedback import FeedbackStore


class FeedbackStoreTests(unittest.TestCase):
    def test_store_keeps_only_aggregate_ratings(self):
        store = FeedbackStore()

        first = store.record("helpful", "EVIDENCE_VERIFIED")
        store.record("not_helpful", "SOURCE_ONLY_FALLBACK")

        self.assertTrue(first["saved"])
        self.assertEqual(store.snapshot(), {
            "total": 2,
            "helpful": 1,
            "not_helpful": 1,
        })

    def test_unknown_rating_is_rejected(self):
        with self.assertRaises(ValueError):
            FeedbackStore().record("maybe")


if __name__ == "__main__":
    unittest.main()
