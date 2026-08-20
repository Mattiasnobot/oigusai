import unittest
from unittest.mock import Mock, patch

from services.ollama_runtime import OllamaRuntimeManager


def response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    return result


class OllamaRuntimeManagerTests(unittest.TestCase):
    def manager(self):
        return OllamaRuntimeManager(
            host="http://ollama.test",
            model="qwen3.5:9b-q4_K_M",
            keep_alive="20m",
            preload_enabled=True,
            preload_timeout=33,
        )

    @patch("services.ollama_runtime.requests.post")
    @patch("services.ollama_runtime.requests.get")
    def test_preload_skips_generate_when_model_is_already_loaded(self, get, post):
        get.side_effect = [
            response({"models": [{"name": "qwen3.5:9b-q4_K_M"}]}),
            response({"models": [{"name": "qwen3.5:9b-q4_K_M", "size_vram": 123}]}),
        ]
        status = self.manager().preload()
        post.assert_not_called()
        self.assertTrue(status["preload_succeeded"])
        self.assertTrue(status["preload_already_loaded"])
        self.assertTrue(status["analysis_model_loaded"])
        self.assertEqual(status["analysis_model_size_vram"], 123)

    @patch("services.ollama_runtime.requests.post")
    @patch("services.ollama_runtime.requests.get")
    def test_preload_uses_empty_generate_request_and_confirms_loaded_state(self, get, post):
        get.side_effect = [
            response({"models": [{"name": "qwen3.5:9b-q4_K_M"}]}),
            response({"models": []}),
            response({"models": [{"name": "qwen3.5:9b-q4_K_M"}]}),
            response({"models": [{"name": "qwen3.5:9b-q4_K_M", "expires_at": "later"}]}),
        ]
        post.return_value = response({"load_duration": 2_500_000_000})
        status = self.manager().preload()
        post.assert_called_once_with(
            "http://ollama.test/api/generate",
            json={
                "model": "qwen3.5:9b-q4_K_M",
                "prompt": "",
                "stream": False,
                "keep_alive": "20m",
            },
            timeout=33,
        )
        self.assertTrue(status["preload_succeeded"])
        self.assertFalse(status["preload_already_loaded"])
        self.assertEqual(status["load_duration_ms"], 2500.0)
        self.assertEqual(status["analysis_model_expires_at"], "later")

    @patch("services.ollama_runtime.requests.get")
    def test_unavailable_ollama_is_reported_without_raising(self, get):
        get.side_effect = ConnectionError("offline")
        status = self.manager().preload()
        self.assertFalse(status["ollama_ready"])
        self.assertFalse(status["preload_succeeded"])
        self.assertIn("offline", status["preload_error"])

    @patch("services.ollama_runtime.requests.get")
    def test_disabled_preload_does_not_probe_ollama(self, get):
        manager = OllamaRuntimeManager(
            host="http://ollama.test",
            model="qwen3.5:9b-q4_K_M",
            preload_enabled=False,
        )
        status = manager.preload()
        self.assertFalse(status["preload_attempted"])
        self.assertFalse(status["preload_enabled"])
        get.assert_not_called()

    def test_model_matching_accepts_default_tag_only_when_config_has_no_tag(self):
        self.assertTrue(OllamaRuntimeManager._matches_model("qwen3.5", "qwen3.5:latest"))
        self.assertFalse(OllamaRuntimeManager._matches_model("qwen3.5:9b", "qwen3.5:latest"))


if __name__ == "__main__":
    unittest.main()
