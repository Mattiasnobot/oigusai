# V11.1 — Curated case-law corpus and offline importer

V11.1 creates the storage/import boundary for future court-practice retrieval.
It does **not** enable court-practice retrieval, model context, or live network
import.

## Source boundary

Public Estonian court decisions are searched through Riigi Teataja, backed by
court information-system publication. V11.1 accepts only HTTPS canonical URLs
on `riigiteataja.ee` / `www.riigiteataja.ee`.

The importer never fetches that URL. Its input is a local JSON array that must be
reviewed outside this script. The importer proves deterministic local identity
and integrity; it does **not** prove that supplied text matches the live page.
A live source adapter belongs to a later separately audited step.

## Raw input fields

Each local import row contains:

- `court_name`
- `case_number`
- `decision_date` (`YYYY-MM-DD`, not future)
- `decision_type`
- `court_level`: `first_instance`, `appeal`, or `supreme`
- `canonical_url`: official Riigi Teataja HTTPS URL
- `text`: reviewed source text

An optional `id` is accepted only if it exactly matches the deterministic ID
computed from case number, decision date, and decision type.

## Committed corpus

- `data/case_law.json` — canonical V11.0 provenance records
- `data/case_law_manifest.json` — byte hash, record count and disabled-feature flags

The first V11.1 commit intentionally contains an empty `[]` corpus. Test records
must never be committed as real court practice.

## Safety properties

The manifest must retain:

- `retrieval_enabled = false`
- `model_context_enabled = false`
- `live_import_enabled = false`
- `authority_status = not_asserted`

`record_sha256` protects record integrity. It does not turn a judgment into a
statute and does not assert binding precedent or any other legal weight.
