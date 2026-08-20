"""Small in-memory guardrails for the local V8.1 pilot runtime."""

from __future__ import annotations

import asyncio
import hmac
import math
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque, Dict, Tuple


class RateLimitExceededError(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Liiga palju päringuid.")
        self.retry_after = max(1, int(retry_after))


class WorkQueueFullError(RuntimeError):
    pass


class WorkQueueTimeoutError(RuntimeError):
    pass


class RuntimeGuard:
    """Access-code checking, bounded request rates and one shared work queue."""

    WINDOW_SECONDS = 60.0

    def __init__(
        self,
        *,
        access_code: str = "",
        rate_limit_per_minute: int = 30,
        upload_limit_per_minute: int = 6,
        max_concurrent_work: int = 1,
        max_queued_work: int = 8,
        queue_timeout: int = 360,
    ):
        self.access_code = str(access_code or "")
        self.rate_limit_per_minute = int(rate_limit_per_minute)
        self.upload_limit_per_minute = int(upload_limit_per_minute)
        self.max_concurrent_work = int(max_concurrent_work)
        self.max_queued_work = int(max_queued_work)
        self.queue_timeout = int(queue_timeout)
        self._requests: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._request_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(self.max_concurrent_work)
        self._active = 0
        self._waiting = 0

    @property
    def access_required(self) -> bool:
        return bool(self.access_code)

    def authorized(self, supplied: str) -> bool:
        if not self.access_required:
            return True
        return hmac.compare_digest(
            self.access_code.encode("utf-8"),
            str(supplied or "").encode("utf-8"),
        )

    def check_rate(self, client_key: str, scope: str, limit: int | None = None) -> None:
        maximum = int(limit or self.rate_limit_per_minute)
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        key = (str(client_key or "unknown")[:160], str(scope or "api")[:40])
        with self._request_lock:
            values = self._requests[key]
            while values and values[0] <= cutoff:
                values.popleft()
            if len(values) >= maximum:
                retry_after = math.ceil(self.WINDOW_SECONDS - (now - values[0]))
                raise RateLimitExceededError(retry_after)
            values.append(now)
            if len(self._requests) > 2000:
                self._prune_rates_locked(cutoff)

    def _prune_rates_locked(self, cutoff: float) -> None:
        stale = []
        for key, values in self._requests.items():
            while values and values[0] <= cutoff:
                values.popleft()
            if not values:
                stale.append(key)
        for key in stale:
            self._requests.pop(key, None)

    @asynccontextmanager
    async def work_slot(self, label: str = "analysis"):
        del label  # Reserved for future metrics without retaining user content.
        with self._counter_lock:
            if self._waiting >= self.max_queued_work and self._active >= self.max_concurrent_work:
                raise WorkQueueFullError("Tööjärjekord on täis.")
            self._waiting += 1

        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.queue_timeout,
                )
                acquired = True
            except TimeoutError as exc:
                raise WorkQueueTimeoutError("Tööjärjekorras ootamine aegus.") from exc
            finally:
                with self._counter_lock:
                    self._waiting = max(0, self._waiting - 1)

            with self._counter_lock:
                self._active += 1
            try:
                yield
            finally:
                with self._counter_lock:
                    self._active = max(0, self._active - 1)
        finally:
            if acquired:
                self._semaphore.release()

    def snapshot(self) -> dict:
        with self._counter_lock:
            return {
                "active": self._active,
                "waiting": self._waiting,
                "max_concurrent": self.max_concurrent_work,
                "max_queued": self.max_queued_work,
            }
