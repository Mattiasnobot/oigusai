# V11.0 — Court-practice provenance boundary

V11.0 introduces only the evidence/provenance architecture for court decisions.
It does **not** add a court-practice corpus, retrieval, importer, prompt context,
or UI exposure.

A case-law record must be supplied as a separate trusted input with:

- `source_kind = case_law`;
- canonical internal `CASE_...` ID;
- court name and case number;
- ISO decision date;
- decision type and court level;
- HTTPS canonical source URL;
- exact source text;
- deterministic `record_sha256` over the audited metadata and text.

The evidence boundary accepts `kind = case_law` only with
`verification_status = CASE_LAW_EVIDENCE_VERIFIED` and only when the exact
excerpt occurs literally in the hash-verified case-law record.

Court-practice sources remain separate from statutory `law` sources. A case-law
record cannot satisfy a law claim, and the existing `law + document`
`INPUTS_VERIFIED` comparison path cannot silently acquire a case-law source.

The verifier also refuses to auto-verify a claim that characterizes a judgment
as a "binding precedent". Legal weight is deliberately `not_asserted` in V11.0
and must be addressed by a later, separately audited policy layer.
