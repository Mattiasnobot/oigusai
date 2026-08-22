"""V11.5 runtime wiring for verified Riigi Teataja model context.

The authoritative admission rules live in ``services.rt_model_context`` and are
used through ``VerifiedLiveModelAnalysisService.prepare_context``. This class is
only a drop-in OfflineAIService wrapper that switches the already-audited model
source list in place before prompt construction.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from config import Settings, load_settings
from services.offline_ai import OfflineAIService
from services.rt_model_context import RTModelContextError
from services.verified_live_analysis import VerifiedLiveModelAnalysisService

V11_5_MODEL_CONTEXT_VERSION = "V11.5-verified-live-model-context-1"


class VerifiedLiveOfflineAIService(OfflineAIService):
    """Opt-in runtime bridge from V11.4 retrieval to the existing Ollama gates."""

    def __init__(
        self,
        *args,
        settings: Settings | None = None,
        live_model_context_enabled: bool | None = None,
        live_context_adapter: VerifiedLiveModelAnalysisService | None = None,
        **kwargs,
    ) -> None:
        cfg = settings or load_settings()
        super().__init__(*args, settings=cfg, **kwargs)
        configured = bool(getattr(cfg, "rt_verified_live_model_context_enabled", False))
        self.live_model_context_enabled = (
            configured
            if live_model_context_enabled is None
            else bool(live_model_context_enabled)
        )
        self.live_context_adapter = live_context_adapter or VerifiedLiveModelAnalysisService(
            ai_service=self
        )
        self._live_context_state = threading.local()
        self._live_context_stats_lock = threading.Lock()
        self._live_context_stats = {
            "attempts": 0,
            "admitted": 0,
            "live_verified": 0,
            "mixed_verified_and_local": 0,
            "local_fallback": 0,
            "unexpected_error": 0,
            "total_context_ms": 0.0,
            "max_context_ms": 0.0,
        }
        self.last_live_model_context = {
            "version": V11_5_MODEL_CONTEXT_VERSION,
            "status": "DISABLED",
            "model_context_enabled": False,
            "mode": "DISABLED",
        }
        self._live_context_logger = logging.getLogger(__name__)

    @property
    def last_live_model_context(self) -> Dict[str, Any]:
        """Return request-thread-local diagnostics for the most recent analysis."""
        return dict(getattr(self._live_context_state, "last", {
            "version": V11_5_MODEL_CONTEXT_VERSION,
            "status": "DISABLED",
            "model_context_enabled": False,
            "mode": "DISABLED",
        }))

    @last_live_model_context.setter
    def last_live_model_context(self, value: Dict[str, Any]) -> None:
        self._live_context_state.last = dict(value)

    def live_model_context_stats(self) -> Dict[str, Any]:
        """Return process-local counters suitable for health/metrics reporting."""
        with self._live_context_stats_lock:
            result = dict(self._live_context_stats)
        attempts = int(result.get("attempts") or 0)
        result["average_context_ms"] = round(
            float(result.get("total_context_ms") or 0.0) / attempts, 1
        ) if attempts else 0.0
        result["max_context_ms"] = round(float(result.get("max_context_ms") or 0.0), 1)
        result["total_context_ms"] = round(float(result.get("total_context_ms") or 0.0), 1)
        return result

    def _record_live_context_outcome(self, outcome: str) -> None:
        with self._live_context_stats_lock:
            self._live_context_stats[outcome] += 1

    def _record_live_context_attempt(self, outcome: str, started: float) -> None:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000)
        with self._live_context_stats_lock:
            self._live_context_stats["attempts"] += 1
            self._live_context_stats[outcome] += 1
            self._live_context_stats["total_context_ms"] += duration_ms
            self._live_context_stats["max_context_ms"] = max(
                self._live_context_stats["max_context_ms"], duration_ms
            )

    def analyze_case_structured(
        self,
        case_desc: str,
        laws: list[Dict],
        event_date: str = "",
        document_spans: list[Dict] | None = None,
    ):
        if self.live_model_context_enabled and laws:
            context_started = time.perf_counter()
            try:
                context = self.live_context_adapter.prepare_context(laws, event_date)
                admitted = list(context.get("laws") or [])
                if admitted:
                    admission = context.get("admission", {})
                    live_count = int(admission.get("live_count") or 0)
                    local_count = int(admission.get("local_count") or 0)
                    mode = (
                        "MIXED_VERIFIED_AND_LOCAL"
                        if live_count and local_count
                        else "LIVE_VERIFIED"
                        if live_count
                        else "LOCAL_FALLBACK"
                    )
                    # Critical invariant: this is the exact list object later used
                    # by SourceVerifier, CoverageVerifier and EvidenceVerifier.
                    laws[:] = admitted
                    self.last_live_model_context = {
                        "version": V11_5_MODEL_CONTEXT_VERSION,
                        "status": context.get("status", "MODEL_CONTEXT_READY"),
                        "live": context.get("live", {}),
                        "admission": context.get("admission", {}),
                        "model_context_enabled": True,
                        "mode": mode,
                    }
                    self._record_live_context_attempt(
                        "mixed_verified_and_local"
                        if mode == "MIXED_VERIFIED_AND_LOCAL"
                        else "live_verified"
                        if mode == "LIVE_VERIFIED"
                        else "local_fallback",
                        context_started,
                    )
                    self._record_live_context_outcome("admitted")
                else:
                    self.last_live_model_context = {
                        "version": V11_5_MODEL_CONTEXT_VERSION,
                        "status": "LOCAL_MODEL_CONTEXT",
                        "model_context_enabled": False,
                        "reason": "no_admitted_live_context",
                        "mode": "LOCAL_FALLBACK",
                    }
                    self._record_live_context_attempt("local_fallback", context_started)
            except (RTModelContextError, ValueError) as exc:
                # Fail closed to the pre-existing audited local law list. No
                # unadmitted live record is copied into ``laws`` on this path.
                self._live_context_logger.warning(
                    "V11.5 live model-context admission failed; keeping audited local corpus: %s",
                    exc,
                )
                self.last_live_model_context = {
                    "version": V11_5_MODEL_CONTEXT_VERSION,
                    "status": "LOCAL_MODEL_CONTEXT",
                    "model_context_enabled": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "mode": "LOCAL_FALLBACK",
                }
                self._record_live_context_attempt("local_fallback", context_started)
            except Exception:
                # Unexpected implementation defects must not look like a healthy
                # local fallback. Let the existing orchestrator error boundary
                # handle them and make the defect observable.
                self._record_live_context_attempt("unexpected_error", context_started)
                self._live_context_logger.exception(
                    "Unexpected V11.5 live model-context runtime failure"
                )
                raise
        return super().analyze_case_structured(
            case_desc,
            laws,
            event_date,
            document_spans,
        )
