import unittest

from verifiers.evidence_verifier import EvidenceVerifier


class EvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = EvidenceVerifier()
        self.laws = [{
            "id": "VOS_308",
            "text": "Üürnik võib maksta tagatisraha osadena.",
        }]
        self.spans = [{
            "span_id": "DOC-1-P1-S1",
            "page": 1,
            "text": "Lepingus on tagatisraha suurus neli kuud.",
        }]

    def test_law_and_document_evidence_must_exist_exactly(self):
        claims = [{
            "claim_id": "INF-1",
            "kind": "inference",
            "text": "Lepingutingimust tuleb võrrelda seadusega.",
            "verification_status": "INPUTS_VERIFIED",
            "sources": [
                {"kind": "law", "id": "VOS_308", "evidence": "Üürnik võib maksta tagatisraha osadena."},
                {"kind": "document", "id": "DOC-1-P1-S1", "page": 1, "evidence": "tagatisraha suurus neli kuud"},
            ],
        }]

        valid, verified = self.verifier.verify(claims, self.laws, self.spans)

        self.assertTrue(valid)
        self.assertEqual(verified[0]["text"], claims[0]["text"])
        self.assertEqual(verified[0]["sources"][0]["title"], "VOS_308")
        offset = self.spans[0]["text"].index("tagatisraha suurus neli kuud")
        self.assertEqual(verified[0]["sources"][1]["start"], offset)
        self.assertEqual(
            verified[0]["sources"][1]["end"],
            offset + len("tagatisraha suurus neli kuud"),
        )

    def test_fabricated_document_span_is_rejected(self):
        claims = [{
            "claim_id": "DOC-1",
            "kind": "document_fact",
            "text": "Vale summa.",
            "verification_status": "DOCUMENT_SPAN_VERIFIED",
            "sources": [{
                "kind": "document",
                "id": "DOC-1-P1-S1",
                "page": 1,
                "evidence": "Summa on 9000 eurot.",
            }],
        }]

        valid, _ = self.verifier.verify(claims, self.laws, self.spans)

        self.assertFalse(valid)

    def test_ocr_excerpt_cannot_be_mislabelled_as_verified_document_text(self):
        spans = [{
            **self.spans[0],
            "method": "ocr",
        }]
        claim = {
            "claim_id": "DOC-1",
            "kind": "document_excerpt",
            "text": "tagatisraha suurus neli kuud",
            "verification_status": "DOCUMENT_TEXT_VERIFIED",
            "sources": [{
                "kind": "document",
                "id": "DOC-1-P1-S1",
                "page": 1,
                "evidence": "tagatisraha suurus neli kuud",
            }],
        }

        valid, _ = self.verifier.verify([claim], self.laws, spans)
        self.assertFalse(valid)

        claim["verification_status"] = "OCR_REVIEW_REQUIRED"
        valid, verified = self.verifier.verify([claim], self.laws, spans)
        self.assertTrue(valid)
        self.assertEqual(verified[0]["sources"][0]["method"], "ocr")


if __name__ == "__main__":
    unittest.main()
