import asyncio
import unittest
from unittest.mock import patch

from services.runtime_guard import (
    RateLimitExceededError,
    RuntimeGuard,
    WorkQueueFullError,
)


class RuntimeGuardTests(unittest.TestCase):
    def test_access_code_uses_exact_constant_time_comparison(self):
        guard = RuntimeGuard(access_code="piisavalt-pikk-kood")

        self.assertTrue(guard.access_required)
        self.assertTrue(guard.authorized("piisavalt-pikk-kood"))
        self.assertFalse(guard.authorized("vale-kood"))

    def test_rate_limit_returns_retry_after(self):
        guard = RuntimeGuard(rate_limit_per_minute=2)
        with patch("services.runtime_guard.time.monotonic", return_value=100.0):
            guard.check_rate("client", "api", 2)
            guard.check_rate("client", "api", 2)
            with self.assertRaises(RateLimitExceededError) as raised:
                guard.check_rate("client", "api", 2)

        self.assertEqual(raised.exception.retry_after, 60)

    def test_queue_rejects_extra_work_when_waiting_is_disabled(self):
        guard = RuntimeGuard(max_concurrent_work=1, max_queued_work=0)

        async def exercise():
            async with guard.work_slot("first"):
                with self.assertRaises(WorkQueueFullError):
                    async with guard.work_slot("second"):
                        pass

        asyncio.run(exercise())
        self.assertEqual(guard.snapshot()["active"], 0)


if __name__ == "__main__":
    unittest.main()
