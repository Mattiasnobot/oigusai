"""V9.1 privacy-preserving runtime quality metrics."""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, deque
from typing import Deque


class QualityMetricsStore:
    """Aggregate operational signals without retaining requests or identifiers."""

    def __init__(self, max_latency_samples: int = 500):
        self.started = time.monotonic()
        self.max_latency_samples = max(20, int(max_latency_samples))
        self._routes = Counter()
        self._statuses = Counter()
        self._verification = Counter()
        self._analysis_latencies: Deque[float] = deque(maxlen=self.max_latency_samples)
        self._fallbacks = 0
        self._analyses = 0
        self._claims = 0
        self._sources = 0
        self._lock = threading.Lock()

    def record_request(self, route_group: str, status_code: int) -> None:
        group = str(route_group or "other")[:40]
        status = f"{int(status_code) // 100}xx"
        with self._lock:
            self._routes[group] += 1
            self._statuses[status] += 1

    def record_analysis(
        self,
        *,
        duration_ms: float,
        verification_status: str,
        fallback: bool,
        claim_count: int,
        source_count: int,
    ) -> None:
        with self._lock:
            self._analyses += 1
            self._fallbacks += int(bool(fallback))
            self._claims += max(0, int(claim_count))
            self._sources += max(0, int(source_count))
            self._verification[str(verification_status or "UNKNOWN")[:48]] += 1
            self._analysis_latencies.append(max(0.0, float(duration_ms)))

    def snapshot(self) -> dict:
        with self._lock:
            latencies = sorted(self._analysis_latencies)
            analyses = self._analyses
            return {
                "uptime_seconds": round(time.monotonic() - self.started, 1),
                "requests_by_route": dict(self._routes),
                "responses_by_status": dict(self._statuses),
                "analyses": analyses,
                "fallbacks": self._fallbacks,
                "fallback_rate": round(self._fallbacks / analyses, 4) if analyses else 0.0,
                "average_claims": round(self._claims / analyses, 2) if analyses else 0.0,
                "average_sources": round(self._sources / analyses, 2) if analyses else 0.0,
                "verification_statuses": dict(self._verification),
                "latency_ms": {
                    "samples": len(latencies),
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                    "max": round(max(latencies), 1) if latencies else 0.0,
                },
                "privacy": "aggregate_only_no_user_text",
            }

    @staticmethod
    def _percentile(values, fraction: float) -> float:
        if not values:
            return 0.0
        index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
        return round(values[index], 1)
