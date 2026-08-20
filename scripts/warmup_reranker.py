"""Download, load and smoke-test the optional ÕigusAI V6.1 reranker."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.reranker import LocalCrossEncoderReranker


def main() -> int:
    settings = load_settings()
    reranker = LocalCrossEncoderReranker(settings=settings)
    if not reranker.enabled:
        print("Reranker is disabled. Set RERANKER_ENABLED=true first.")
        return 2

    candidates = [
        (
            1.0,
            {
                "id": "TEST_TLS",
                "law_name": "Töölepingu seadus",
                "title": "Töölepingu seadus § 88",
                "text": "Tööandja võib töölepingu erakorraliselt üles öelda töötajast tuleneval mõjuval põhjusel.",
            },
            {},
        ),
        (
            0.9,
            {
                "id": "TEST_VOS",
                "law_name": "Võlaõigusseadus",
                "title": "Võlaõigusseadus § 308",
                "text": "Üürnik võib tagatisraha maksta kolme kuu jooksul võrdsetes osades.",
            },
            {},
        ),
    ]
    started = time.perf_counter()
    ranked = reranker.rerank(
        "Kas tööandja võib mind päevapealt vallandada?", candidates
    )
    first_elapsed = time.perf_counter() - started
    repeat_times = []
    for _ in range(5):
        repeat_started = time.perf_counter()
        reranker.rerank(
            "Kas tööandja võib mind päevapealt vallandada?", candidates
        )
        repeat_times.append(time.perf_counter() - repeat_started)
    print(json.dumps({
        "status": reranker.status(),
        "first_elapsed_seconds": round(first_elapsed, 3),
        "repeat_median_seconds": round(sorted(repeat_times)[2], 3),
        "ranking": [
            {"id": law["id"], "score": round(score, 4)}
            for score, law, _ in ranked
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if ranked and ranked[0][1]["id"] == "TEST_TLS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
