# V11.3 — Riigi Teataja authority and currentness gate

V11.3 promotes an exact V11.2.1 official Riigi Teataja XML source to a binding legal source only when the exact revision also passes an audited authority and validity gate.

The safety rule remains:

> **NO AUTHORITATIVE SOURCE OF THE RIGHT CLASS → NO LEGAL CLAIM**

## What V11.3 verifies

For one exact RT act/revision identifier and one requested legal date, V11.3 requires explicit revision metadata for:

- issuer;
- act type;
- revision validity start;
- revision validity end (including an explicit open-ended value);
- publication marker;
- optional text type retained in provenance.

Only these mappings may become `binding_rule` sources:

- `RT I` + `seadus` or `määrus` → `RT_NATIONAL_LAW` / `binding_national_law`;
- `RT IV` + `määrus` → `RT_LOCAL_LAW` / `binding_local_law`.

RT II/III documents, KOV decisions, national orders and other act types are not silently promoted into the binding-rule class.

## Revision validity semantics

`valid_from` is inclusive. Riigi Teataja presents a revision's `kehtivuse lõpp` as the boundary at which the next state begins, so V11.3 treats `valid_to` as exclusive. A revision ending on `2026-08-21` therefore cannot support a claim for `2026-08-21`.

Future-date assertions are disabled. An open-ended current revision can be verified only for today or an earlier date, never for an unknown future state.

## Provenance

A successful result retains:

- exact RT act id and canonical URL;
- XML and normalized-text SHA-256 from V11.2.1;
- issuer, act type, text type and publication marker;
- canonical validity interval;
- registry source id and authority class;
- a deterministic `revision_provenance_sha256` over the audited metadata and source hashes.

## Still disabled

V11.3 does **not**:

- resolve an arbitrary historical act id/title to the latest/current revision;
- make future-date legal assertions;
- write `data/laws.json`;
- write the case-law corpus;
- enable live retrieval;
- expose live RT text to model context.

Those are separate audited stages.

## Verification

```bash
python -m unittest tests.test_rt_authority -v
python scripts/verify_rt_authority.py
python scripts/verify_rt_authority.py --live-url https://www.riigiteataja.ee/akt/106032026003
```
