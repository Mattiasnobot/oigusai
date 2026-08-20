# ÕigusAI V6.1 reranker baseline — 2026-08-11

## Scope

V6.1 adds a local multilingual query-passage reranker after the verified V6
hybrid candidate retrieval. The chosen model is
`BAAI/bge-reranker-v2-m3` (Apache-2.0, 0.6B parameters).

The reranker:

- receives only candidate records already mapped back to the checksum-verified
  `data/laws.json` corpus;
- cannot create a law ID, source or legal text;
- evaluates at most 20 candidates per query variant;
- interleaves the best ranks of separately understood query clauses before RRF;
- is loaded lazily and stays in the application process after the first query;
- keeps the unchanged V6 ranking if dependencies, model loading, CUDA memory or
  inference fail.

## Locked runtime configuration

```text
RERANKER_ENABLED=true
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE=auto
RERANKER_CANDIDATES=20
RERANKER_BATCH_SIZE=8
RERANKER_MAX_LENGTH=512
RERANKER_MAX_CHARS=5000
RERANKER_WEIGHT=2.0
```

Measured device: NVIDIA GeForce RTX 3060 12 GB, CUDA.

## Original 200-case suite

| Metric | V6 | V6.1 |
|---|---:|---:|
| Overall | 183/200 (91.5%) | **184/200 (92.0%)** |
| Expected behaviour | 200/200 | **200/200** |
| Domain-any Recall@5 | 170/170 | **170/170** |
| Domain-all Recall@5 | 26/30 | **26/30** |
| Section-any Recall@5 | 136/140 | **136/140** |
| Section-group Recall@5 | 17/30 | **18/30** |
| Challenge split | 20/20 | **20/20** |
| Development split | 120/120 | **120/120** |
| Holdout split | 43/60 | **44/60** |
| Retrieval p50 | 956 ms | **1226 ms** |
| Retrieval p95 | 1784 ms | **2425 ms** |

The maximum 9790 ms includes lazy model loading in a fresh process. A local
two-passage smoke test measured a 9.05 second cached cold load and 12 ms median
repeat inference. Real warm V6.1 example searches measured 1.3–2.1 seconds.

## Fresh frozen post-tuning holdout

The configuration above was frozen before running
`eval/query_cases_v61_frozen_holdout.json`. Its SHA-256 and first-run protocol
are recorded in `eval/V61_FROZEN_HOLDOUT_2026-08-11.md`.

- overall: **29/30 (96.7%)**
- expected behaviour: **30/30 (100%)**
- domain-any Recall@5: **27/27 (100%)**
- section-any Recall@5: **26/27 (96.3%)**
- fail-closed: **3/3 (100%)**

No ranking, labels, lexicon or thresholds were changed after this result.

## Verification

- 93/93 unit and regression tests passed.
- `/health` after a live query reported:
  - `hybrid_ready=true`
  - `vector_rows=22287`
  - `reranker_loaded=true`
  - `reranker_ready=true`
  - `reranker_device=cuda`
- live `POST /analyze` returned HTTP 200, `CITATIONS_VERIFIED`, `is_mock=false`.
- browser QA passed the one-word clarification flow and a complete free-form
  question-to-verified-answer flow; no browser console warnings or errors.

## Known limits

- V6.1 improves the section-group metric by one case; it does not solve every
  cross-domain paraphrase. Missing target sections outside the V6 candidate set
  cannot be recovered by a reranker.
- The first query after application restart is visibly slower while the 2.3 GB
  reranker model is loaded from local disk to GPU.
- `CITATIONS_VERIFIED` still verifies the source/evidence boundary, not the full
  material correctness of every legal conclusion.
