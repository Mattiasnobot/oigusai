# V11.5 — Verified live evidence → model context

V11.5 adds the first explicit model-context admission boundary for Riigi
Teataja live evidence and wires it into the normal application AI service behind
an opt-in runtime flag.

## Trust chain

```text
audited local candidate
  -> V11.4 current-revision resolution
  -> BINDING_SECTION_VERIFIED
  -> V11.5 model-context admission
  -> existing OfflineAIService prompt
  -> existing SourceVerifier / EvidenceVerifier / CoverageVerifier
```

A live record is admitted only when all of the following still match at the
model boundary:

- `verification_status == BINDING_SECTION_VERIFIED`
- `evidence_source == rt_live_verified`
- audited RT source/authority mapping
- `authority_verified == true`
- `currentness_verified == true`
- exact canonical, XML and section-anchor Riigi Teataja URLs
- exact requested legal date
- section text SHA-256 recomputed from the exact text
- V11.4 section provenance chain recomputed
- non-persistent live-source policy

`LOCAL_CORPUS_FALLBACK` may remain available to the model only when it is byte-
for-byte equivalent on audited fields to the original local corpus candidate.
It is never relabelled as live evidence.

## Same source set for model and verifiers

`main.py` now instantiates `VerifiedLiveOfflineAIService`, a subclass of the
existing `OfflineAIService`. When live model context is enabled, the wrapper
runs V11.4 retrieval + V11.5 admission and replaces the contents of the existing
`analysis_laws` list **in place** before the prompt is built.

The `AnalysisOrchestrator` itself is intentionally unchanged. Because the same
list object continues through the existing request, Ollama and downstream
source/evidence/coverage verification see the exact same legal records.

If retrieval or admission fails, no live record is copied into that list and
the pre-existing audited local records remain in use.

## Runtime switch

Application wiring exists, but remains disabled by default:

```env
RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED=false
```

For explicit live-model evaluation set:

```env
RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED=true
```

No live source is written to `laws.json`, no persistent live cache is added,
and future-date live assertions remain disabled.

## Verification

Offline contract and deterministic tests:

```powershell
python scripts/verify_rt_model_context.py
python -m unittest tests.test_rt_model_context tests.test_verified_live_analysis tests.test_verified_live_model_context -v
```

Explicit live admission:

```powershell
python scripts/verify_rt_model_context.py `
  --live-title "Töölepingu seadus" `
  --section 95 `
  --as-of 2026-08-21 `
  --domain TLS
```

Explicit Ollama smoke against admitted live evidence:

```powershell
python scripts/verify_rt_model_context.py `
  --live-title "Töölepingu seadus" `
  --section 95 `
  --as-of 2026-08-21 `
  --domain TLS `
  --model-smoke `
  --question "Kas töölepingu võib üles öelda ainult suuliselt?"
```
