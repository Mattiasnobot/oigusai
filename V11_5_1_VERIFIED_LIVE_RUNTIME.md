# V11.5.1 — Opt-in verified-live runtime wiring

V11.5 introduced the explicit model-context admission gate for Riigi Teataja
live evidence. V11.5.1 wires that already-audited gate into the normal
application AI service while keeping the feature disabled by default.

## Runtime path

```text
existing LegalSearchService candidates
  -> V11.4 verified current-revision retrieval
  -> V11.5 model-context admission
  -> same analysis_laws list object
  -> existing OfflineAIService prompt
  -> existing SourceVerifier / EvidenceVerifier / CoverageVerifier
```

`main.py` uses `VerifiedLiveOfflineAIService`, which remains a subclass of the
existing `OfflineAIService`. `AnalysisOrchestrator` is intentionally unchanged.

When live context is enabled, the wrapper calls the V11.5
`VerifiedLiveModelAnalysisService.prepare_context()` adapter before the prompt is
built. Admitted records replace the contents of the existing `analysis_laws`
list **in place**. The model and every downstream verifier therefore see the
same legal source objects.

If live retrieval or admission raises, the list is not changed and the original
audited local corpus records remain the model context. No failed or unadmitted
live record is copied into the list.

## Runtime switch

The feature remains disabled by default:

```env
RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED=false
```

Enable it only for explicit live-model evaluation:

```env
RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED=true
```

The setting is read from the central `config.py`. CI leaves it unset, so hosted
deterministic tests keep the existing local-corpus runtime path.

## Safety boundaries

- V11.5 `BINDING_SECTION_VERIFIED` admission remains mandatory.
- V11.5 recomputes live content and section provenance before model admission.
- Local fallback must still match the original audited corpus candidate.
- Future-date live assertions remain disabled.
- No live text is written to `laws.json`.
- No persistent live cache is introduced.
- Existing model-output source and evidence verification remains unchanged.

## Verification

Offline runtime contract and targeted tests:

```powershell
python scripts/verify_rt_model_runtime.py
python -m unittest tests.test_verified_live_model_context -v
```

The V11.5 explicit live/model smoke remains available:

```powershell
python scripts/verify_rt_model_context.py `
  --live-title "Töölepingu seadus" `
  --section 95 `
  --as-of 2026-08-21 `
  --domain TLS `
  --model-smoke `
  --question "Kas töölepingu võib üles öelda ainult suuliselt?"
```

V11.5.1 additionally provides a smoke through the **normal runtime wrapper**:

```powershell
python scripts/verify_rt_model_runtime.py `
  --runtime-model-smoke `
  --source-id TLS_95 `
  --as-of 2026-08-21 `
  --question "Kas töölepingu võib üles öelda ainult suuliselt?"
```

That smoke starts with the audited local `TLS_95` candidate, exercises the
V11.4 → V11.5 live admission path, mutates the shared law list in place, calls
the real local Ollama model, and finally runs the existing `SourceVerifier`.
