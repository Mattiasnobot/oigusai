import json
import unittest
from collections import Counter
from types import SimpleNamespace

from scripts.build_query_evaluation_set import LAWS_FILE, build_cases, validate_cases
from scripts.evaluate_queries import evaluate_case, select_cases
from services.legal_search import HistoricalDataUnavailableError


class _FakeSearchService:
    def __init__(self, laws=None, historical_error=False):
        self._laws = laws or []
        self._historical_error = historical_error

    def search_laws_with_context(self, query, event_date):
        if self._historical_error:
            raise HistoricalDataUnavailableError("historical corpus unavailable")
        return self._laws, SimpleNamespace(notes=["test interpretation"])


class QueryEvaluationDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laws = json.loads(LAWS_FILE.read_text(encoding="utf-8"))
        cls.cases = build_cases()

    def test_audited_set_has_fixed_size_and_splits(self):
        self.assertEqual(len(self.cases), 200)
        self.assertEqual(
            Counter(case["split"] for case in self.cases),
            {"development": 120, "holdout": 60, "challenge": 20},
        )
        self.assertEqual(len({case["id"] for case in self.cases}), 200)

    def test_all_labels_validate_against_local_corpus(self):
        validate_cases(self.cases, self.laws)

    def test_retrieval_cases_have_section_level_labels(self):
        retrieval_cases = [
            case for case in self.cases if case["expected_behavior"] == "retrieve"
        ]
        self.assertEqual(len(retrieval_cases), 170)
        self.assertTrue(
            all(
                case["expected_sections_any"] or case["expected_section_groups"]
                for case in retrieval_cases
            )
        )


class QueryEvaluatorTests(unittest.TestCase):
    def test_case_selection_keeps_dataset_order_and_rejects_unknown_ids(self):
        cases = [{"id": "A"}, {"id": "B"}, {"id": "C"}]

        self.assertEqual(
            [case["id"] for case in select_cases(cases, ["C", "A"])],
            ["A", "C"],
        )
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            select_cases(cases, ["UNKNOWN"])

    def test_multi_law_case_requires_every_domain_and_section_group(self):
        service = _FakeSearchService(
            [
                {"id": "TLS_4", "domain": "TLS"},
                {"id": "VOS_14", "domain": "VOS"},
            ]
        )
        case = {
            "id": "CROSS-TEST",
            "query": "test",
            "expected_behavior": "retrieve",
            "expected_domains": ["TLS", "VOS"],
            "expected_domains_all": ["TLS", "VOS"],
            "expected_sections_any": [],
            "expected_section_groups": [["TLS_4"], ["VOS_14"]],
            "tags": ["cross_domain"],
        }

        result = evaluate_case(service, case, {"TLS", "VOS"}, {"TLS_4", "VOS_14"})

        self.assertTrue(result["overall_ok"])
        self.assertTrue(result["checks"]["domain_all"])
        self.assertTrue(result["checks"]["section_groups"])

    def test_no_result_is_an_expected_behavior_not_a_retrieval_failure(self):
        result = evaluate_case(
            _FakeSearchService(),
            {
                "id": "NONE-TEST",
                "query": "test",
                "expected_behavior": "no_result",
                "tags": ["fail_closed"],
            },
        )
        self.assertTrue(result["overall_ok"])
        self.assertEqual(result["actual_behavior"], "no_result")

    def test_historical_unavailability_is_measured_explicitly(self):
        result = evaluate_case(
            _FakeSearchService(historical_error=True),
            {
                "id": "HIST-TEST",
                "query": "test",
                "event_date": "2010-01-01",
                "expected_behavior": "historical_unavailable",
                "tags": ["historical"],
            },
        )
        self.assertTrue(result["overall_ok"])
        self.assertEqual(result["actual_behavior"], "historical_unavailable")
        self.assertTrue(all(value is None for value in result["checks"].values()))

    def test_v5_domain_only_schema_remains_supported(self):
        result = evaluate_case(
            _FakeSearchService([{"id": "VOS_308", "domain": "VOS"}]),
            {
                "id": "LEGACY-TEST",
                "query": "tagatisraha",
                "expected_domains": ["VOS"],
                "tag": "legacy",
            },
            {"VOS"},
            {"VOS_308"},
        )
        self.assertTrue(result["overall_ok"])
        self.assertEqual(result["tags"], ["legacy"])


if __name__ == "__main__":
    unittest.main()
