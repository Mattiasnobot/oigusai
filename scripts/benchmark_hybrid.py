"""Measure ÕigusAI V6 retrieval latency without generating an AI answer."""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.legal_search import LegalSearchService


DEFAULT_QUERIES = [
    "Kas tööandja võib mind päevapealt vallandada?",
    "Kuidas vaidlustada haldusakti esmalt vaidemenetluses ja seejärel kohtus?",
    "Kuidas seostuvad lapse elatise maksmine ja võlgniku sissetuleku arestimise piirid?",
]


def _measure(service: LegalSearchService, query: str) -> tuple[float, list[str]]:
    started = time.perf_counter()
    laws, _ = service.search_laws_with_context(query, "")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, [law["id"] for law in laws]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", default=[])
    args = parser.parse_args()
    queries = args.query or DEFAULT_QUERIES

    settings = load_settings()
    hybrid = LegalSearchService(data_file=settings.legal_data_file, settings=settings)
    lexical = LegalSearchService(
        data_file=settings.legal_data_file,
        settings=replace(settings, hybrid_retrieval_enabled=False),
    )
    if hybrid.hybrid_ready:
        hybrid.vector_search.embedding_service.embed_texts(["ÕigusAI soojendus"])

    hybrid_times = []
    lexical_times = []
    for query in queries:
        lexical_ms, lexical_ids = _measure(lexical, query)
        variants = hybrid._dense_query_variants(query)
        dense_started = time.perf_counter()
        hybrid.vector_search.search_many(variants)
        dense_ms = (time.perf_counter() - dense_started) * 1000.0
        hybrid_ms, hybrid_ids = _measure(hybrid, query)
        lexical_times.append(lexical_ms)
        hybrid_times.append(hybrid_ms)
        print(f"Query: {query}")
        print(f"  V5 lexical: {lexical_ms:.1f} ms -> {lexical_ids}")
        print(f"  Dense only: {dense_ms:.1f} ms ({len(variants)} variant(s))")
        print(f"  V6 hybrid:  {hybrid_ms:.1f} ms -> {hybrid_ids}")

    print(
        f"Median: lexical={statistics.median(lexical_times):.1f} ms, "
        f"hybrid={statistics.median(hybrid_times):.1f} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
