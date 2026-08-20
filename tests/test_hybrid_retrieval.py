import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import load_settings
from services.embedding_service import EmbeddingServiceError, OllamaEmbeddingService
from services.legal_search import LegalSearchService
from services.reranker import RerankerUnavailableError
from services.vector_search import (
    DenseSearchResult,
    LanceDBVectorSearch,
    VectorSearchUnavailableError,
)


class _FakeVectorSearch:
    def __init__(self, results=None, error=None):
        self.ready = True
        self.error = None
        self.row_count = 6
        self.embedding_dimension = 3
        self.model = "test-embedding"
        self._results = results or []
        self._search_error = error

    def search(self, query):
        if self._search_error:
            raise VectorSearchUnavailableError(self._search_error)
        return self._results

    def status(self):
        return {
            "enabled": True,
            "ready": self.ready,
            "embedding_model": self.model,
            "embedding_dimension": self.embedding_dimension,
            "vector_rows": self.row_count,
            "error": self.error,
        }


class _FakeReranker:
    def __init__(self, promoted_id=None, error=None):
        self.model_name = "test-reranker"
        self.device = "cpu"
        self.candidates = 20
        self.loaded = True
        self.error = None
        self.promoted_id = promoted_id
        self._rerank_error = error
        self.seen_ids = []

    def rerank(self, query, ranking):
        self.seen_ids = [item[1]["id"] for item in ranking]
        if self._rerank_error:
            raise RerankerUnavailableError(self._rerank_error)
        return sorted(
            ranking,
            key=lambda item: (
                item[1]["id"] != self.promoted_id,
                item[1]["id"],
            ),
        )

    def status(self):
        return {
            "enabled": True,
            "loaded": True,
            "ready": not bool(self._rerank_error),
            "model": self.model_name,
            "device": self.device,
            "candidates": self.candidates,
            "error": self._rerank_error,
        }


class HybridRetrievalTests(unittest.TestCase):
    def _record(self, law_id):
        text = "Lepingu tingimused ja lubatud kokkulepped."
        return {
            "id": law_id,
            "title": f"Testseadus § {law_id.split('_', 1)[1]}",
            "text": text,
            "source": "Riigi Teataja: TEST",
            "domain": "TEST",
            "law_name": "Testseadus",
            "section": law_id.split("_", 1)[1],
            "aliases": ["leping"],
            "url": f"https://www.riigiteataja.ee/akt/TEST#para{law_id}",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def _service(self, directory, vector_search, reranker=None):
        records = [self._record(f"TEST_{number}") for number in range(1, 7)]
        path = Path(directory) / "laws.json"
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        settings = load_settings({
            "LEGAL_MIN_SCORE": "1",
            "LEGAL_MAX_RESULTS": "5",
            "LEGAL_RELATIVE_THRESHOLD": "0",
            "HYBRID_RETRIEVAL_ENABLED": "true",
            "RERANKER_ENABLED": "true" if reranker is not None else "false",
        })
        return LegalSearchService(
            data_file=path,
            settings=settings,
            vector_search=vector_search,
            reranker=reranker,
        )

    def test_dense_rank_can_promote_a_real_corpus_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._record("TEST_6")
            dense = _FakeVectorSearch([
                DenseSearchResult("TEST_6", target["content_hash"], 0.1)
            ])
            service = self._service(tmp, dense)

            results = service.search_laws("Kas leping on lubatud?", "")

            self.assertIn("TEST_6", [law["id"] for law in results])

    def test_stale_dense_record_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            dense = _FakeVectorSearch([
                DenseSearchResult("TEST_6", "stale-content-hash", 0.1)
            ])
            service = self._service(tmp, dense)

            results = service.search_laws("Kas leping on lubatud?", "")

            self.assertNotIn("TEST_6", [law["id"] for law in results])

    def test_dense_failure_uses_the_same_lexical_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            failed = self._service(tmp, _FakeVectorSearch(error="Ollama unavailable"))
            lexical = self._service(tmp, _FakeVectorSearch(results=[]))

            failed_ids = [law["id"] for law in failed.search_laws("Kas leping on lubatud?", "")]
            lexical_ids = [law["id"] for law in lexical.search_laws("Kas leping on lubatud?", "")]

            self.assertEqual(failed_ids, lexical_ids)

    def test_stale_manifest_is_rejected_before_lancedb_is_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "index"
            index_dir.mkdir()
            (index_dir / "current.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "corpus_fingerprint": "wrong",
                    "embedding_model": "test-embedding",
                    "embedding_max_chars": 6000,
                    "row_count": 1,
                }),
                encoding="utf-8",
            )
            settings = load_settings({
                "VECTOR_INDEX_DIR": str(index_dir),
                "EMBEDDING_MODEL": "test-embedding",
            })
            laws = [self._record("TEST_1")]

            vector_search = LanceDBVectorSearch(settings=settings, laws=laws)

            self.assertFalse(vector_search.ready)
            self.assertIn("ei vasta praegusele", vector_search.error)

    def test_embedding_response_shape_is_validated(self):
        service = OllamaEmbeddingService(
            host="http://localhost:11434",
            model="test-embedding",
            timeout=10,
            batch_size=2,
            keep_alive="1m",
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"embeddings": [[0.1, 0.2]]}

        with patch("services.embedding_service.httpx.post", return_value=response):
            with self.assertRaises(EmbeddingServiceError):
                service.embed_texts(["üks", "kaks"])

    def test_query_variants_do_not_duplicate_punctuation_stripped_full_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, _FakeVectorSearch(results=[]))

            simple = service._dense_query_variants("Kas leping on lubatud?")
            compound = service._dense_query_variants(
                "Kaup oli katki ja müüja ei vasta kaebusele."
            )

            self.assertEqual(simple, ["Kas leping on lubatud?"])
            self.assertEqual(len(compound), 3)

    def test_reranker_promotes_only_a_verified_hybrid_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._record("TEST_6")
            dense = _FakeVectorSearch([
                DenseSearchResult("TEST_6", target["content_hash"], 0.1)
            ])
            reranker = _FakeReranker(promoted_id="TEST_6")
            service = self._service(tmp, dense, reranker=reranker)

            results = service.search_laws("Kas leping on lubatud?", "")

            self.assertEqual(results[0]["id"], "TEST_6")
            self.assertEqual(set(reranker.seen_ids), {f"TEST_{n}" for n in range(1, 7)})

    def test_reranker_failure_preserves_v6_result_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._record("TEST_6")
            dense_results = [
                DenseSearchResult("TEST_6", target["content_hash"], 0.1)
            ]
            baseline = self._service(tmp, _FakeVectorSearch(dense_results))
            baseline_ids = [
                law["id"] for law in baseline.search_laws("Kas leping on lubatud?", "")
            ]

        with tempfile.TemporaryDirectory() as tmp:
            failing = self._service(
                tmp,
                _FakeVectorSearch(dense_results),
                reranker=_FakeReranker(error="out of memory"),
            )
            failing_ids = [
                law["id"] for law in failing.search_laws("Kas leping on lubatud?", "")
            ]

        self.assertEqual(failing_ids, baseline_ids)

    def test_variant_ranks_keep_each_clause_champion_near_the_top(self):
        records = [
            (1.0, self._record(f"TEST_{number}"), {})
            for number in range(1, 6)
        ]

        combined = LegalSearchService._round_robin_rankings([
            [records[0], records[1], records[2]],
            [records[2], records[3], records[4]],
        ])

        self.assertEqual(
            [item[1]["id"] for item in combined],
            ["TEST_1", "TEST_3", "TEST_2", "TEST_4", "TEST_5"],
        )


if __name__ == "__main__":
    unittest.main()
