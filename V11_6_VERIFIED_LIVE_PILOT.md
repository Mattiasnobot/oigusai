# V11.6 — Verified-live pilot and observability

V11.6 keeps verified-live model context disabled by default while making an
explicit 20–50 case local pilot measurable and user-visible.

## Runtime signals

- API responses expose `legal_context.mode` as `LIVE_VERIFIED`,
  `MIXED_VERIFIED_AND_LOCAL`, `LOCAL_FALLBACK`, or `DISABLED`.
- The `model_analysis` pipeline stage exposes the same mode without retaining
  user text.
- `/health` and `/admin/metrics` expose aggregate attempt, outcome, error and
  context-latency counters.
- The chat UI states whether the answer used current verified RT evidence,
  mixed evidence, or the audited local fallback.

## Controlled pilot

Start ÕigusAI with the explicit opt-in:

```env
RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED=true
```

Then run 20 cases through the normal `/analyze` API:

```powershell
python scripts/evaluate_rt_live_pilot.py --limit 20 --as-of 2026-08-22
```

The JSON report contains only case IDs, response status, legal-context mode,
latency and source count. It does not retain questions, answers or legal text.
The default acceptance gate requires all requests to succeed, every response to
declare a known context mode, at least one verified-live result, and no more
than 25% local fallback. V11.6 does not change the runtime default.
