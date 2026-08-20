"""Bounded Ollama model lifecycle management for ÕigusAI V10.3.3.

The analysis model can be preloaded at application startup so the first user
request does not pay the full model cold-load cost.  This module never pulls or
downloads models, and preload failures are diagnostic rather than fatal.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests


class OllamaRuntimeManager:
    """Inspect and optionally preload one configured local Ollama model."""

    def __init__(
        self,
        *,
        host: str,
        model: str,
        keep_alive: str = "10m",
        preload_enabled: bool = True,
        preload_timeout: int = 180,
        status_timeout: float = 2.0,
    ) -> None:
        self.host = str(host or "http://localhost:11434").rstrip("/")
        self.model = str(model or "").strip()
        self.keep_alive = str(keep_alive or "10m").strip()
        self.preload_enabled = bool(preload_enabled)
        self.preload_timeout = max(1, int(preload_timeout))
        self.status_timeout = max(0.2, float(status_timeout))
        self._warmup: Dict[str, Any] = {
            "preload_enabled": self.preload_enabled,
            "preload_attempted": False,
            "preload_succeeded": False,
            "preload_already_loaded": False,
            "preload_seconds": 0.0,
            "load_duration_ms": None,
            "preload_error": None,
        }

    @staticmethod
    def _name(value: Any) -> str:
        return str(value or "").strip().casefold()

    @classmethod
    def _matches_model(cls, configured: str, actual: str) -> bool:
        wanted = cls._name(configured)
        candidate = cls._name(actual)
        if not wanted or not candidate:
            return False
        if wanted == candidate:
            return True
        if ":" not in wanted:
            return candidate.split(":", 1)[0] == wanted
        return False

    @staticmethod
    def _model_name(record: Dict[str, Any]) -> str:
        return str(record.get("name") or record.get("model") or "").strip()

    def _installed_models(self) -> List[Dict[str, Any]]:
        response = requests.get(f"{self.host}/api/tags", timeout=self.status_timeout)
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return [item for item in models if isinstance(item, dict)]

    def loaded_models(self) -> List[Dict[str, Any]]:
        """Return Ollama's current in-memory model records from ``/api/ps``."""
        response = requests.get(f"{self.host}/api/ps", timeout=self.status_timeout)
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return [item for item in models if isinstance(item, dict)]

    def _find_model(
        self,
        records: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        return next(
            (
                record
                for record in records
                if self._matches_model(self.model, self._model_name(record))
            ),
            None,
        )

    @staticmethod
    def _load_duration_ms(payload: Any) -> Optional[float]:
        if not isinstance(payload, dict):
            return None
        raw = payload.get("load_duration")
        if raw is None:
            return None
        try:
            # Ollama duration values are nanoseconds.
            return round(float(raw) / 1_000_000.0, 3)
        except (TypeError, ValueError):
            return None

    def snapshot(self) -> Dict[str, Any]:
        """Return bounded runtime state without raising when Ollama is unavailable."""
        installed: List[Dict[str, Any]] = []
        loaded: List[Dict[str, Any]] = []
        error: Optional[str] = None
        ollama_ready = False

        try:
            installed = self._installed_models()
            ollama_ready = True
        except Exception as exc:  # readiness must stay fail-open
            error = str(exc)[:240]

        if ollama_ready:
            try:
                loaded = self.loaded_models()
            except Exception as exc:
                error = str(exc)[:240]

        installed_record = self._find_model(installed)
        loaded_record = self._find_model(loaded)
        status = {
            "ollama_ready": ollama_ready,
            "analysis_model_ready": installed_record is not None,
            "analysis_model_loaded": loaded_record is not None,
            "analysis_model_size_vram": (
                loaded_record.get("size_vram") if loaded_record else None
            ),
            "analysis_model_expires_at": (
                loaded_record.get("expires_at") if loaded_record else None
            ),
            "ollama_runtime_error": error,
        }
        status.update(self._warmup)
        return status

    def preload(self) -> Dict[str, Any]:
        """Preload the configured model with an empty generate request.

        No model is pulled automatically.  Any failure is captured in the returned
        state so application startup can continue in degraded mode.
        """
        if not self.preload_enabled:
            self._warmup.update({
                "preload_attempted": False,
                "preload_succeeded": False,
                "preload_already_loaded": False,
                "preload_seconds": 0.0,
                "load_duration_ms": None,
                "preload_error": None,
            })
            status = {
                "ollama_ready": False,
                "analysis_model_ready": False,
                "analysis_model_loaded": False,
                "analysis_model_size_vram": None,
                "analysis_model_expires_at": None,
                "ollama_runtime_error": None,
            }
            status.update(self._warmup)
            return status

        self._warmup["preload_attempted"] = True
        before = self.snapshot()
        if not before.get("ollama_ready"):
            self._warmup["preload_error"] = before.get("ollama_runtime_error")
            before.update(self._warmup)
            return before
        if not before.get("analysis_model_ready"):
            self._warmup["preload_error"] = (
                f"Mudelit '{self.model}' ei leitud Ollamast."
            )
            before.update(self._warmup)
            return before
        if before.get("analysis_model_loaded"):
            self._warmup.update({
                "preload_succeeded": True,
                "preload_already_loaded": True,
                "preload_seconds": 0.0,
                "load_duration_ms": 0.0,
                "preload_error": None,
            })
            before.update(self._warmup)
            return before

        started = time.perf_counter()
        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": self.keep_alive,
                },
                timeout=self.preload_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            self._warmup.update({
                "preload_seconds": round(time.perf_counter() - started, 3),
                "load_duration_ms": self._load_duration_ms(payload),
                "preload_error": None,
            })
        except Exception as exc:  # startup must not depend on Ollama warmup
            self._warmup.update({
                "preload_succeeded": False,
                "preload_already_loaded": False,
                "preload_seconds": round(time.perf_counter() - started, 3),
                "load_duration_ms": None,
                "preload_error": str(exc)[:240],
            })
            before.update(self._warmup)
            return before

        after = self.snapshot()
        loaded = bool(after.get("analysis_model_loaded"))
        self._warmup.update({
            "preload_succeeded": loaded,
            "preload_already_loaded": False,
            "preload_error": (
                None if loaded else "Ollama vastas warmupile, kuid mudelit /api/ps loendis ei ole."
            ),
        })
        after.update(self._warmup)
        return after
