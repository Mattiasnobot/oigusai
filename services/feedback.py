"""Privacy-preserving in-memory pilot feedback counters."""

from __future__ import annotations

import threading
from collections import Counter


class FeedbackStore:
    """Keep only aggregate ratings; never retain case text or document data."""

    ALLOWED = {"helpful", "not_helpful"}

    def __init__(self):
        self._counts = Counter()
        self._lock = threading.Lock()

    def record(self, rating: str, verification_status: str = "") -> dict:
        normalized = str(rating or "").strip().casefold()
        if normalized not in self.ALLOWED:
            raise ValueError("Tagasiside väärtus ei ole lubatud.")
        status = str(verification_status or "unknown").strip().upper()[:48] or "UNKNOWN"
        with self._lock:
            self._counts[(normalized, status)] += 1
            total = sum(self._counts.values())
        return {"saved": True, "total": total}

    def snapshot(self) -> dict:
        with self._lock:
            helpful = sum(
                value for (rating, _), value in self._counts.items()
                if rating == "helpful"
            )
            not_helpful = sum(
                value for (rating, _), value in self._counts.items()
                if rating == "not_helpful"
            )
        return {
            "total": helpful + not_helpful,
            "helpful": helpful,
            "not_helpful": not_helpful,
        }
