# V6.1 frozen post-tuning holdout

Freeze date: 2026-08-11

Dataset: `eval/query_cases_v61_frozen_holdout.json`

SHA-256 at freeze:
`2682887559627C5B938DA4B255A3BC7AF764983A80168B44E5E0F2C7A5969BF6`

The 30 cases were written and frozen after the V6.1 candidate count and fusion
weight had been selected, but before this dataset was run. The set contains 27
retrieval cases whose section IDs were not used as labels in the original
200-case suite, plus three out-of-scope fail-closed cases.

This dataset is evaluation-only. Its first-run result must be reported without
changing the V6.1 ranking, query lexicon, labels or thresholds in response.

## First run (unchanged frozen configuration)

- overall: 29/30 (96.7%)
- expected behaviour: 30/30 (100%)
- domain-any Recall@5: 27/27 (100%)
- section-any Recall@5: 26/27 (96.3%)
- fail-closed: 3/3 (100%)
- latency: p50 1575.7 ms, p95 3202.9 ms, max 12450.9 ms (cold model load)
- only failure: `V61-FH-017` retrieved the correct MKS domain but not `MKS_4`
  in the first five results.

No retrieval settings, labels or query lexicon were changed after this run.
