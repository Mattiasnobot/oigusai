import unittest
from unittest.mock import patch

from config import load_settings
from services.offline_ai import OfflineAIService


class VersionCompatibilityTests(unittest.TestCase):
    def test_offline_ai_loads_central_settings_without_injected_settings(self):
        env = {
            "OLLAMA_MODEL": "test-model",
            "OLLAMA_NUM_CTX": "4096",
            "OLLAMA_NUM_PREDICT": "777",
            "OLLAMA_THINK": "false",
            "OLLAMA_TEMPERATURE": "0.15",
            "OLLAMA_TOP_P": "0.8",
            "OLLAMA_TIMEOUT": "123",
            "OLLAMA_KEEP_ALIVE": "7m",
            "OLLAMA_CITATION_RETRIES": "2",
            "ALLOW_MOCK_ANALYSIS": "false",
        }
        settings = load_settings(env)
        with patch("services.offline_ai.load_settings", return_value=settings):
            service = OfflineAIService()

        self.assertEqual(service.model_name, "test-model")
        self.assertEqual(service.num_ctx, 4096)
        self.assertEqual(service.num_predict, 777)
        self.assertFalse(service.think)
        self.assertEqual(service.timeout, 123)
        self.assertEqual(service.keep_alive, "7m")
        self.assertEqual(service.citation_retries, 2)


if __name__ == "__main__":
    unittest.main()
