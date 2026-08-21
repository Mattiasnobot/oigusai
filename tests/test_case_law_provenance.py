import copy
import unittest

from services.case_law_provenance import (
    CaseLawProvenanceVerifier,
    compute_case_law_record_sha256,
)
from verifiers.evidence_verifier import EvidenceVerifier


class CaseLawProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "source_kind": "case_law",
            "id": "CASE_TEST_001",
            "court_name": "Testkohus",
            "case_number": "TEST-1-23",
            "decision_date": "2026-01-15",
            "decision_type": "otsus",
            "court_level": "test-level",
            "canonical_url": "https://example.invalid/case/TEST-1-23",
            "text": (
                "Kohus selgitas, et asja lahendamisel tuleb hinnata kõiki "
                "tõendeid nende kogumis."
            ),
        }
        self.record["record_sha256"] = compute_case_law_record_sha256(self.record)

    def test_exact_case_law_record_and_excerpt_pass(self):
        claim = {
            "claim_id": "CASE-1",
            "kind": "case_law",
            "text": "Kohus käsitles tõendite kogumis hindamist.",
            "verification_status": "CASE_LAW_EVIDENCE_VERIFIED",
            "sources": [{
                "kind": "case_law",
                "id": "CASE_TEST_001",
                "evidence": "asja lahendamisel tuleb hinnata kõiki tõendeid nende kogumis",
            }],
        }

        valid, verified = EvidenceVerifier().verify(
            [claim], [], (), [self.record]
        )

        self.assertTrue(valid)
        source = verified[0]["sources"][0]
        self.assertEqual(source["case_number"], "TEST-1-23")
        self.assertEqual(source["authority_status"], "not_asserted")
        self.assertEqual(source["record_sha256"], self.record["record_sha256"])

    def test_tampered_record_hash_fails_closed(self):
        tampered = copy.deepcopy(self.record)
        tampered["text"] += " Muudetud."

        valid, _ = CaseLawProvenanceVerifier().verify_record(tampered)

        self.assertFalse(valid)

    def test_case_law_claim_requires_audited_record(self):
        claim = {
            "claim_id": "CASE-1",
            "kind": "case_law",
            "text": "Kohus käsitles tõendite kogumis hindamist.",
            "verification_status": "CASE_LAW_EVIDENCE_VERIFIED",
            "sources": [{
                "kind": "case_law",
                "id": "CASE_TEST_001",
                "evidence": "asja lahendamisel tuleb hinnata kõiki tõendeid nende kogumis",
            }],
        }

        valid, _ = EvidenceVerifier().verify([claim], [])

        self.assertFalse(valid)

    def test_case_law_cannot_masquerade_as_law(self):
        claim = {
            "claim_id": "LAW-1",
            "kind": "law",
            "text": "Vale allikaliik.",
            "verification_status": "EVIDENCE_VERIFIED",
            "sources": [{
                "kind": "case_law",
                "id": "CASE_TEST_001",
                "evidence": "asja lahendamisel tuleb hinnata kõiki tõendeid nende kogumis",
            }],
        }

        valid, _ = EvidenceVerifier().verify([claim], [], (), [self.record])

        self.assertFalse(valid)

    def test_binding_precedent_language_is_not_auto_verified(self):
        claim = {
            "claim_id": "CASE-1",
            "kind": "case_law",
            "text": "See lahend on siduv pretsedent kõigile kohtutele.",
            "verification_status": "CASE_LAW_EVIDENCE_VERIFIED",
            "sources": [{
                "kind": "case_law",
                "id": "CASE_TEST_001",
                "evidence": "asja lahendamisel tuleb hinnata kõiki tõendeid nende kogumis",
            }],
        }

        valid, _ = EvidenceVerifier().verify([claim], [], (), [self.record])

        self.assertFalse(valid)

    def test_existing_inference_cannot_smuggle_case_law_source(self):
        laws = [{"id": "TEST_1", "text": "Seaduse täpne tekst."}]
        spans = [{
            "span_id": "DOC-1-P1-S1",
            "page": 1,
            "text": "Dokumendi täpne tekst.",
        }]
        claim = {
            "claim_id": "INF-1",
            "kind": "inference",
            "text": "Sisendeid võrreldakse.",
            "verification_status": "INPUTS_VERIFIED",
            "sources": [
                {"kind": "law", "id": "TEST_1", "evidence": "Seaduse täpne tekst."},
                {"kind": "document", "id": "DOC-1-P1-S1", "evidence": "Dokumendi täpne tekst."},
                {"kind": "case_law", "id": "CASE_TEST_001", "evidence": "asja lahendamisel tuleb hinnata kõiki tõendeid nende kogumis"},
            ],
        }

        valid, _ = EvidenceVerifier().verify(
            [claim], laws, spans, [self.record]
        )

        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
