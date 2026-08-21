# V11.4 — Riigi Teataja current revision + verified live retrieval

V11.4 closes the gap between natural-language corpus retrieval and a currently verified official Riigi Teataja provision.

## Trust chain

1. Existing deterministic/hybrid retrieval selects candidate laws and sections from the audited local corpus.
2. `RTCurrentRevisionResolver` queries only the official RT search endpoint for the requested legal date and exact act title.
3. Every candidate act ID is re-fetched through the V11.2.1 exact XML adapter and must pass the V11.3 authority/currentness gate.
4. The official title in the verified XML must exactly match after conservative normalization.
5. Only explicitly requested sections are extracted from those same verified XML bytes.
6. Each section receives a content hash and a section provenance hash chained to the verified revision provenance.

## Fail-closed rules

- Future-date resolution is disabled.
- Search redirects may not leave the official RT HTTPS host or audited search path.
- Zero exact candidates fail closed.
- Multiple exact current candidates fail closed as ambiguous.
- RT II/III and non-binding act types remain blocked by V11.3.
- Missing or duplicate section identifiers fail closed.
- Live failures may fall back only to the already audited local corpus and must be labeled `LOCAL_CORPUS_FALLBACK`.

## Deliberate non-features

- No writes to `data/laws.json` or the case-law corpus.
- No persistent live-response cache.
- No live text is enabled for model context in V11.4.
- Normal `LegalSearchService` runtime behavior is unchanged. `VerifiedLiveLegalSearch` is an explicit composition layer so the audited retrieval baseline remains isolated.

The next gate can wire only `BINDING_SECTION_VERIFIED` evidence into model context after separate acceptance testing.
