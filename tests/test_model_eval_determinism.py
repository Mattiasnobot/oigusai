import unittest
from unittest.mock import Mock, patch

from config import load_settings
from scripts.evaluate_model import MODEL_EVAL_SEED, MODEL_EVAL_TEMPERATURE, _runtime_environment
from services.offline_ai import OfflineAIService


class ModelEvalDeterminismTests(unittest.TestCase):
    def _payload(self, *, generation_seed=None):
        settings = load_settings({
            "OLLAMA_HOST": "http://example.test:11434",
            "OLLAMA_MODEL": "unit-model",
            "OLLAMA_TIMEOUT": "30",
            "OLLAMA_TEMPERATURE": "0.1",
            "OLLAMA_TOP_P": "0.9",
            "OLLAMA_NUM_CTX": "2048",
            "OLLAMA_NUM_PREDICT": "256",
            "OLLAMA_THINK": "false",
            "OLLAMA_KEEP_ALIVE": "5m",
        })
        ai = OfflineAIService(
            settings=settings,
            allow_mock=False,
            generation_seed=generation_seed,
        )
        response = Mock(status_code=200)
        response.json.return_value = {"response": "OK"}
        with patch("services.offline_ai.requests.post", return_value=response) as post:
            self.assertEqual(ai._call_ollama("test prompt"), "OK")
        return post.call_args.kwargs["json"]

    def test_normal_runtime_omits_generation_seed(self):
        payload = self._payload()
        self.assertNotIn("seed", payload["options"])

    def test_explicit_generation_seed_is_forwarded_to_ollama(self):
        payload = self._payload(generation_seed=MODEL_EVAL_SEED)
        self.assertEqual(payload["options"]["seed"], MODEL_EVAL_SEED)

    def test_model_eval_pins_temperature_and_seed(self):
        env = _runtime_environment(lexical_only=False)
        self.assertEqual(float(env["OLLAMA_TEMPERATURE"]), MODEL_EVAL_TEMPERATURE)
        self.assertEqual(MODEL_EVAL_SEED, 42)
        self.assertEqual(MODEL_EVAL_TEMPERATURE, 0.0)


if __name__ == "__main__":
    unittest.main()
