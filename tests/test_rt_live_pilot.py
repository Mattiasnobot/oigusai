from __future__ import annotations

import json
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.evaluate_rt_live_pilot import run_pilot


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *, unexpected_after=0):
        self.unexpected_after = unexpected_after

    def get(self, *_args, **_kwargs):
        value = getattr(self, "health_calls", 0)
        self.health_calls = value + 1
        return FakeResponse({
            "verified_live_context": {
                "unexpected_error": 0 if value == 0 else self.unexpected_after
            }
        })

    def post(self, *_args, **_kwargs):
        return FakeResponse({
            "legal_context": {"mode": "LIVE_VERIFIED"},
            "sources_used": ["TLS_95"],
        })


class LivePilotEvaluationTests(unittest.TestCase):
    def test_twenty_case_report_is_private_and_passes(self):
        with TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases.json"
            cases.write_text(json.dumps([
                {"id": f"CASE-{index}", "query": f"private question {index}"}
                for index in range(20)
            ]), encoding="utf-8")
            args = Namespace(
                base_url="http://test",
                cases=str(cases),
                limit=20,
                as_of="2026-08-22",
                timeout=30,
                max_local_fallback_rate=0.25,
            )
            with patch(
                "scripts.evaluate_rt_live_pilot.requests.Session",
                return_value=FakeSession(),
            ):
                report = run_pilot(args)

        self.assertTrue(report["acceptance_passed"])
        self.assertFalse(report["retains_user_text"])
        self.assertEqual(report["mode_counts"], {"LIVE_VERIFIED": 20})
        self.assertNotIn("private question", json.dumps(report))

    def test_unexpected_runtime_error_fails_acceptance(self):
        with TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases.json"
            cases.write_text(json.dumps([
                {"id": f"CASE-{index}", "query": f"question {index}"}
                for index in range(20)
            ]), encoding="utf-8")
            args = Namespace(
                base_url="http://test",
                cases=str(cases),
                limit=20,
                as_of="2026-08-22",
                timeout=30,
                max_local_fallback_rate=0.25,
            )
            with patch(
                "scripts.evaluate_rt_live_pilot.requests.Session",
                return_value=FakeSession(unexpected_after=1),
            ):
                report = run_pilot(args)

        self.assertFalse(report["acceptance_passed"])
        self.assertEqual(report["unexpected_live_errors"], 1)


if __name__ == "__main__":
    unittest.main()
