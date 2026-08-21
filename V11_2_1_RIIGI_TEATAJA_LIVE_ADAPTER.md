# V11.2.1 — Riigi Teataja exact live-source adapter

V11.2.1 adds an **on-demand verifier** for one exact Riigi Teataja act XML source.
It does not change the legal corpus, case-law corpus, retrieval ranking, or model context.

## Audited 2026 endpoint

Riigi Teataja's official FAQ states that from **1 June 2026** individual act XML is no longer served as `/akt/{id}.xml`; live XML is available through:

`https://www.riigiteataja.ee/public-api/api/v1/akt/{act_id}/xml`

The existing general legal-act search API remains separate and unchanged. V11.2.1 uses only the exact numeric XML endpoint; current-revision resolution is deliberately deferred.

## What this stage proves

For an explicit numeric act id or exact HTTPS Riigi Teataja act URL the adapter:

- derives the official XML API URL;
- allows only `riigiteataja.ee` / `www.riigiteataja.ee`;
- rejects credentials, query strings, fragments, non-numeric act paths and cross-host redirects;
- limits response size;
- rejects DTD/entity declarations;
- requires valid XML, an exact matching act id in XML metadata, and an auditable title;
- returns SHA-256 hashes for the exact XML bytes and normalized text.

The result status is `OFFICIAL_SOURCE_VERIFIED`.

## What this stage does **not** prove

A successful fetch does **not** by itself prove:

- that the fetched revision is the revision currently in force;
- that it is national rather than local-government law;
- that the source supports a specific legal conclusion;
- that the text may enter retrieval/model context.

Therefore every result retains:

- `authority_class = not_asserted`
- `currentness_verified = false`
- `retrieval_enabled = false`
- `model_context_enabled = false`
- `corpus_write_enabled = false`

This prevents an exact official source from being promoted into a binding current-law claim before later audited classification and effective-date resolution.

## Verification

Offline CI contract check (no network):

```powershell
python scripts/verify_rt_live_adapter.py
```

Optional explicit live smoke test:

```powershell
python scripts/verify_rt_live_adapter.py --live-url https://www.riigiteataja.ee/akt/106032026003
```

A live smoke test verifies the exact source only; it does not mutate any corpus.
