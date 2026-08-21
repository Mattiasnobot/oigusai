# V11.2 — Official Legal Source Registry

V11.2 introduces a fail-closed registry for official and institutional legal-information sources.
It does **not** enable live network adapters, retrieval integration, or model context.

The core rule is stricter than "NO SOURCE → NO LEGAL CLAIM":

> **NO AUTHORITATIVE SOURCE OF THE RIGHT CLASS → NO LEGAL CLAIM**

## Authority classes

- `binding_national_law` — current Estonian national law; may support `binding_rule` claims.
- `binding_local_law` — current local-government law; may support `binding_rule` claims.
- `binding_eu_law` — current EU law; may support `binding_rule` claims.
- `judicial_decision` — a court holding; may support `court_holding`, not a statutory rule claim.
- `legislative_history` — parliamentary procedure/history; may support only legislative-history claims.
- `draft_legislation` — a draft; may support only draft claims.
- `official_notice` — an official notice/publication event.
- `institutional_interpretation` — an institution's stated interpretation/position.
- `regulator_guidance` — official regulator guidance/reference material, not a substitute for the binding text.
- `secondary_analysis` — analysis of case law, not the judgment itself.

## Registered source families

The first registry contains 23 source families covering:

- Riigi Teataja national law, local law, and court decisions;
- Ametlikud Teadaanded;
- Riigikogu proceedings and EIS drafts;
- Riigikohus decisions and case-law analyses;
- Õiguskantsler positions;
- EUR-Lex / Official Journal of the EU;
- CURIA and HUDOC;
- TTJA, AKI, Tööinspektsioon, EMTA, Finantsinspektsioon, Eesti Pank,
  Konkurentsiamet, Transpordiamet, Keskkonnaamet, Ravimiamet, and Päästeamet.

The registry is intentionally extensible. Adding another official authority does not require changing
retrieval architecture; it requires a deliberate registry change, authority classification, host allowlist,
and tests.

## Binding-rule safety boundary

Only these source families are currently permitted to support a `binding_rule` claim:

- `RT_NATIONAL_LAW`
- `RT_LOCAL_LAW`
- `EURLEX_EU_LAW`

For example, TTJA or Tööinspektsioon may explain or collect links to applicable legislation, but their
web guidance must not silently become the canonical statutory text in an answer. A draft from EIS or
Riigikogu likewise cannot support a claim phrased as current binding law.

## Disabled integration boundary

Every source entry must retain:

- `live_adapter_enabled = false`
- `retrieval_enabled = false`
- `model_context_enabled = false`

The global manifest also keeps live adapters, retrieval integration, and model-context integration disabled.
V11.2 therefore changes classification/audit metadata only. Network adapters are activated one source family
at a time in later audited steps.
