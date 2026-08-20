import unittest

from config import ConfigurationError, load_settings


class ConfigTests(unittest.TestCase):
    def test_model_and_runtime_values_come_from_environment(self):
        settings = load_settings({
            "OLLAMA_MODEL": "test-model",
            "APP_ACCESS_CODE": "turvaline-test-kood",
            "APP_RATE_LIMIT_PER_MINUTE": "44",
            "APP_UPLOAD_LIMIT_PER_MINUTE": "7",
            "APP_MAX_CONCURRENT_WORK": "2",
            "APP_MAX_QUEUED_WORK": "5",
            "APP_QUEUE_TIMEOUT": "222",
            "MATTER_TTL_MINUTES": "90",
            "OLLAMA_TIMEOUT": "321",
            "OLLAMA_TEMPERATURE": "0.15",
            "OLLAMA_TOP_P": "0.8",
            "OLLAMA_NUM_CTX": "4096",
            "OLLAMA_NUM_PREDICT": "777",
            "OLLAMA_THINK": "true",
            "OLLAMA_KEEP_ALIVE": "20m",
            "OLLAMA_CITATION_RETRIES": "2",
            "OLLAMA_VISION_MODEL": "test-vision",
            "OLLAMA_OCR_TIMEOUT": "222",
            "LEGAL_MIN_SCORE": "4",
            "LEGAL_MAX_RESULTS": "3",
            "LEGAL_RELATIVE_THRESHOLD": "0.7",
            "QUERY_UNDERSTANDING_ENABLED": "true",
            "QUERY_LEXICON_FILE": "data/query_lexicon.json",
            "QUERY_FUZZY_THRESHOLD": "0.84",
            "QUERY_FUZZY_MAX_MATCHES": "2",
            "QUERY_FUZZY_MIN_TOKEN_LENGTH": "6",
            "QUERY_MAX_EXPANDED_TERMS": "12",
            "QUERY_COMPOUND_ENABLED": "false",
            "QUERY_DOMAIN_HINT_BONUS": "3",
            "QUERY_CURATED_DOMAIN_HINT_BONUS": "9",
            "HYBRID_RETRIEVAL_ENABLED": "true",
            "VECTOR_INDEX_DIR": "data/test-lancedb",
            "EMBEDDING_MODEL": "test-embedding",
            "EMBEDDING_TIMEOUT": "77",
            "EMBEDDING_BATCH_SIZE": "8",
            "EMBEDDING_KEEP_ALIVE": "5m",
            "EMBEDDING_MAX_CHARS": "5000",
            "HYBRID_DENSE_CANDIDATES": "40",
            "HYBRID_RRF_K": "30",
            "HYBRID_LEXICAL_WEIGHT": "1.5",
            "HYBRID_DENSE_WEIGHT": "0.75",
            "HYBRID_DIVERSITY_WEIGHT": "0.5",
            "HYBRID_MULTI_QUERY_ENABLED": "true",
            "HYBRID_MAX_QUERY_VARIANTS": "3",
            "RERANKER_ENABLED": "true",
            "RERANKER_MODEL": "test-reranker",
            "RERANKER_DEVICE": "cpu",
            "RERANKER_CANDIDATES": "15",
            "RERANKER_BATCH_SIZE": "4",
            "RERANKER_MAX_LENGTH": "384",
            "RERANKER_MAX_CHARS": "4000",
            "RERANKER_WEIGHT": "1.75",
        })
        self.assertEqual(settings.ollama_model, "test-model")
        self.assertEqual(settings.app_access_code, "turvaline-test-kood")
        self.assertEqual(settings.app_rate_limit_per_minute, 44)
        self.assertEqual(settings.app_upload_limit_per_minute, 7)
        self.assertEqual(settings.app_max_concurrent_work, 2)
        self.assertEqual(settings.app_max_queued_work, 5)
        self.assertEqual(settings.app_queue_timeout, 222)
        self.assertEqual(settings.matter_ttl_minutes, 90)
        self.assertEqual(settings.ollama_timeout, 321)
        self.assertAlmostEqual(settings.ollama_temperature, 0.15)
        self.assertAlmostEqual(settings.ollama_top_p, 0.8)
        self.assertEqual(settings.ollama_num_ctx, 4096)
        self.assertEqual(settings.ollama_num_predict, 777)
        self.assertTrue(settings.ollama_think)
        self.assertEqual(settings.ollama_keep_alive, "20m")
        self.assertEqual(settings.ollama_citation_retries, 2)
        self.assertEqual(settings.ollama_vision_model, "test-vision")
        self.assertEqual(settings.ollama_ocr_timeout, 222)
        self.assertEqual(settings.legal_min_score, 4)
        self.assertEqual(settings.legal_max_results, 3)
        self.assertAlmostEqual(settings.legal_relative_threshold, 0.7)
        self.assertTrue(settings.query_understanding_enabled)
        self.assertEqual(settings.query_lexicon_file.name, "query_lexicon.json")
        self.assertAlmostEqual(settings.query_fuzzy_threshold, 0.84)
        self.assertEqual(settings.query_fuzzy_max_matches, 2)
        self.assertEqual(settings.query_fuzzy_min_token_length, 6)
        self.assertEqual(settings.query_max_expanded_terms, 12)
        self.assertFalse(settings.query_compound_enabled)
        self.assertEqual(settings.query_domain_hint_bonus, 3)
        self.assertEqual(settings.query_curated_domain_hint_bonus, 9)
        self.assertTrue(settings.hybrid_retrieval_enabled)
        self.assertEqual(settings.vector_index_dir.name, "test-lancedb")
        self.assertEqual(settings.embedding_model, "test-embedding")
        self.assertEqual(settings.embedding_timeout, 77)
        self.assertEqual(settings.embedding_batch_size, 8)
        self.assertEqual(settings.embedding_keep_alive, "5m")
        self.assertEqual(settings.embedding_max_chars, 5000)
        self.assertEqual(settings.hybrid_dense_candidates, 40)
        self.assertEqual(settings.hybrid_rrf_k, 30)
        self.assertAlmostEqual(settings.hybrid_lexical_weight, 1.5)
        self.assertAlmostEqual(settings.hybrid_dense_weight, 0.75)
        self.assertAlmostEqual(settings.hybrid_diversity_weight, 0.5)
        self.assertTrue(settings.hybrid_multi_query_enabled)
        self.assertEqual(settings.hybrid_max_query_variants, 3)
        self.assertTrue(settings.reranker_enabled)
        self.assertEqual(settings.reranker_model, "test-reranker")
        self.assertEqual(settings.reranker_device, "cpu")
        self.assertEqual(settings.reranker_candidates, 15)
        self.assertEqual(settings.reranker_batch_size, 4)
        self.assertEqual(settings.reranker_max_length, 384)
        self.assertEqual(settings.reranker_max_chars, 4000)
        self.assertAlmostEqual(settings.reranker_weight, 1.75)

    def test_invalid_boolean_fails_clearly(self):
        with self.assertRaises(ConfigurationError):
            load_settings({"OLLAMA_THINK": "maybe"})

    def test_invalid_numeric_range_fails_clearly(self):
        with self.assertRaises(ConfigurationError):
            load_settings({"OLLAMA_TOP_P": "1.5"})
        with self.assertRaises(ConfigurationError):
            load_settings({"APP_PORT": "70000"})
        with self.assertRaises(ConfigurationError):
            load_settings({"QUERY_FUZZY_THRESHOLD": "0.2"})
        with self.assertRaises(ConfigurationError):
            load_settings({"RERANKER_DEVICE": "internet"})
        with self.assertRaises(ConfigurationError):
            load_settings({"APP_ACCESS_CODE": "lühike"})

    def test_relative_paths_are_resolved_under_project(self):
        settings = load_settings({"LEGAL_DATA_FILE": "data/custom.json"})
        self.assertTrue(settings.legal_data_file.is_absolute())
        self.assertEqual(settings.legal_data_file.name, "custom.json")


if __name__ == "__main__":
    unittest.main()
