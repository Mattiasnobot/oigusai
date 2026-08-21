# V11.5 — Verified live evidence → model context

V11.5 adds the first explicit model-context admission boundary for Riigi
Teataja live evidence.

## Trust chain

```text
audited local candidate
  -> V11.4 current-revision resolution
  -> BINDING_SECTION_VERIFIED
  -> V11.5 model-context admission
  -> existing OfflineAIService prompt
  -> existing source/evidence verification
```

A live record is admitted only when all of the following still match at the
model boundary:

- `verification_status == BINDING_SECTION_VERIFIED`
- `evidence_source == rt_live_verified`
- audited RT source/authority mapping
- `authority_verified == true`
- `currentness_verified == true`
- exact `canonical_url`, `xml_url`, and section URL
- exact legal date
- section text SHA-256
- V11.4 section provenance chain
- non-persistent live-source policy

`LOCAL_CORPUS_FALLBACK` can remain available to the model, but only when the
fallback record is identical to the original audited corpus candidate. It is
never relabelled as live evidence.

## Scope boundary

V11.5 provides an explicit `VerifiedLiveModelAnalysisService` adapter and a
live/model smoke verifier. Normal `/analyze` runtime wiring remains disabled in
this step. This keeps the existing deterministic retrieval and model-evaluation
baseline unchanged while the live model boundary is tested separately.

No live source is written to `laws.json`, and no persistent live cache is
introduced.
