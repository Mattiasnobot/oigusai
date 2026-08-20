import unittest

from verifiers.source_verifier import SourceVerifier


class SourceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = SourceVerifier()
        self.laws = [
            {"id": "AVTS_10", "title": "Avaliku teabe seadus § 10", "text": "..."},
            {"id": "VOS_308", "title": "Võlaõigusseadus § 308", "text": "..."},
            {"id": "VOS_3B2", "title": "Võlaõigusseadus § 3²", "text": "..."},
        ]

    def test_accepts_known_mixed_case_citation(self):
        text = (
            "OLUKORD:\nKüsimus puudutab lepingu lõpetamist.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Seaduse kohaldamisel tuleb kontrollida vastavat tingimust [avts_10].\n\n"
            "SOOVITUSED:\nKontrolli dokumente.\n\n"
            "KASUTATUD ALLIKAD: [AvTS_10]"
        )
        valid, sources = self.verifier.verify_sources(text, self.laws)
        self.assertTrue(valid)
        self.assertEqual(sources, ["AVTS_10"])

    def test_rejects_unknown_citation(self):
        text = (
            "OLUKORD:\nTest.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\nVäide tugineb allikale [FAKE_999].\n\n"
            "SOOVITUSED:\nKontrolli.\n\n"
            "KASUTATUD ALLIKAD: [FAKE_999]"
        )
        valid, _ = self.verifier.verify_sources(text, self.laws)
        self.assertFalse(valid)

    def test_rejects_single_citation_covering_multiple_sentences(self):
        text = (
            "OLUKORD:\nTest.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Esimene õiguslik väide. Teine õiguslik väide. Kolmas väide [VOS_308].\n\n"
            "SOOVITUSED:\nKontrolli.\n\n"
            "KASUTATUD ALLIKAD: [VOS_308]"
        )
        valid, _ = self.verifier.verify_sources(text, self.laws)
        self.assertFalse(valid)

    def test_rejects_uncited_application_paragraph(self):
        text = (
            "OLUKORD:\nTest.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Esimene õiguslik väide on viidatud [VOS_308].\n"
            "Teine õiguslik väide jääb viiteta.\n\n"
            "SOOVITUSED:\nKontrolli.\n\n"
            "KASUTATUD ALLIKAD: [VOS_308]"
        )
        valid, _ = self.verifier.verify_sources(text, self.laws)
        self.assertFalse(valid)

    def test_rejects_unbracketed_law_reference(self):
        text = (
            "OLUKORD:\nTest.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "VÕS järgi kehtib nõue.\n"
            "Teine väide on viidatud [VOS_308].\n\n"
            "SOOVITUSED:\nKontrolli.\n\n"
            "KASUTATUD ALLIKAD: [VOS_308]"
        )
        valid, _ = self.verifier.verify_sources(text, self.laws)
        self.assertFalse(valid)

    def test_accepts_superscript_section_citation_key(self):
        text = (
            "OLUKORD:\nTest.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Õiguslik väide tugineb allikale [VOS_3B2].\n\n"
            "SOOVITUSED:\nKontrolli.\n\n"
            "KASUTATUD ALLIKAD: [VOS_3B2]"
        )
        valid, sources = self.verifier.verify_sources(text, self.laws)
        self.assertTrue(valid)
        self.assertEqual(sources, ["VOS_3B2"])


if __name__ == "__main__":
    unittest.main()
