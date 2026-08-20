import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from config import load_settings
from services.legal_search import LegalSearchService
from services.query_understanding import QueryUnderstandingService


class QueryUnderstandingTests(unittest.TestCase):
    def _law(self, *, law_id="ABIPOLS_12", domain="ABIPOLS"):
        text = "Meetme kohaldamise tingimused ja lubatud tegevused."
        return {
            "id": law_id,
            "title": "Abipolitseiniku seadus § 12",
            "text": text,
            "source": "Riigi Teataja: ABIPOLS",
            "domain": domain,
            "law_name": "Abipolitseiniku seadus",
            "section": "12",
            "aliases": ["abipolitseinik", "korrakaitse"],
            "url": "https://www.riigiteataja.ee/akt/APolS?leiaKehtiv#para12",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def _tls_law(self):
        text = "Töösuhte lõpetamise tingimused."
        return {
            "id": "TLS_90",
            "title": "Töölepingu seadus § 90",
            "text": text,
            "source": "Riigi Teataja: TLS",
            "domain": "TLS",
            "law_name": "Töölepingu seadus",
            "section": "90",
            "aliases": ["tööleping", "töötaja", "tööandja"],
            "url": "https://www.riigiteataja.ee/akt/TLS?leiaKehtiv#para90",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def test_typo_abipoliteinuku_is_expanded_from_corpus_vocabulary(self):
        service = QueryUnderstandingService([self._law()])
        result = service.analyze("Kas abipoliteinuku võib mind kinni pidada?")

        self.assertIn("abipolitseiniku", result.expanded_tokens)
        self.assertIn("ABIPOLS", result.domain_hints)
        self.assertTrue(any(match.original == "abipoliteinuku" for match in result.matches))
        self.assertTrue(any("abipoliteinuku" in note for note in result.notes))

    def test_split_compound_is_joined_only_for_known_legal_term(self):
        service = QueryUnderstandingService([self._law()])
        result = service.analyze("Kas abi politseiniku tegevus oli lubatud?")

        self.assertIn("abipolitseiniku", result.expanded_tokens)
        compound = [match for match in result.matches if match.reason == "compound"]
        self.assertTrue(compound)
        self.assertEqual(compound[0].candidate, "abipolitseiniku")

    def test_unrelated_word_is_not_forced_into_legal_vocabulary(self):
        service = QueryUnderstandingService([self._law()])
        result = service.analyze("Minu maasikamoos kukkus põrandale")

        self.assertEqual(result.matches, [])
        self.assertEqual(result.notes, [])

    def test_suffix_variant_is_not_misreported_as_typo(self):
        law = self._law()
        law["law_name"] = "Avalda seadus"
        law["title"] = "Avalda seadus § 12"
        service = QueryUnderstandingService([law])
        result = service.analyze("Ettevõte avaldas teate")

        self.assertIn("avalda", result.expanded_tokens)
        self.assertFalse(any(match.reason == "fuzzy" for match in result.matches))
        self.assertFalse(any("kirjaviga" in note for note in result.notes))

    def test_curated_domain_hint_cannot_create_source_without_text_match(self):
        unrelated = self._law(law_id="PS_1", domain="PS")
        unrelated["law_name"] = "Testiseadus"
        unrelated["title"] = "Testiseadus § 1"
        unrelated["aliases"] = ["katse"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "laws.json"
            path.write_text(json.dumps([unrelated], ensure_ascii=False), encoding="utf-8")
            settings = load_settings({
                "LEGAL_MIN_SCORE": "6",
                "QUERY_UNDERSTANDING_ENABLED": "true",
                "QUERY_CURATED_DOMAIN_HINT_BONUS": "20",
            })
            service = LegalSearchService(data_file=path, settings=settings)

            self.assertEqual(service.search_laws("Kas minu põhiõigusi piirati?", ""), [])

    def test_disabled_query_understanding_preserves_original_search_behavior(self):
        service = QueryUnderstandingService([self._law()], enabled=False)
        result = service.analyze("abipoliteinuku")

        self.assertEqual(result.expanded_tokens, [])
        self.assertEqual(result.matches, [])

    def test_curated_estonian_lexicon_maps_natural_language_to_retrieval_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "laws.json"
            path.write_text(json.dumps([self._tls_law()], ensure_ascii=False), encoding="utf-8")
            settings = load_settings({
                "LEGAL_MIN_SCORE": "6",
                "LEGAL_RELATIVE_THRESHOLD": "0.6",
                "QUERY_UNDERSTANDING_ENABLED": "true",
            })
            service = LegalSearchService(data_file=path, settings=settings)
            laws, interpretation = service.search_laws_with_context(
                "Mind vallandati päevapealt", ""
            )

            self.assertEqual([law["id"] for law in laws], ["TLS_90"])
            self.assertIn("vallandamine", interpretation.expanded_tokens)
            self.assertIn("TLS", interpretation.domain_hints)
            self.assertTrue(any(match.reason == "lexicon" for match in interpretation.matches))

    def test_curated_section_hint_is_exposed_and_keeps_real_corpus_record(self):
        service = QueryUnderstandingService(
            [self._tls_law()],
            lexicon_entries=[{
                "forms": ["tood pole"],
                "expand": ["koondamine"],
                "domains": ["TLS"],
                "sections": ["TLS_90"],
            }],
        )

        result = service.analyze("Mind lasti lahti, sest tood pole")

        self.assertEqual(result.section_hints, ["TLS_90"])
        self.assertIn("koondamine", result.expanded_tokens)

    def test_flexible_auxiliary_police_fine_keeps_procedure_sections(self):
        ids = [
            "ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114"
        ]
        laws = [
            self._law(law_id=law_id, domain=law_id.split("_", 1)[0])
            for law_id in ids
        ]
        service = QueryUnderstandingService(laws)

        result = service.analyze(
            "Abipolitsei tegi mulle niisama seismise eest trahvi. "
            "Kas seda saab vaidlustada?"
        )

        self.assertEqual(set(result.section_hints), set(ids))
        self.assertIn("VTMS", result.domain_hints)
        self.assertIn("kaebuse tähtaeg", result.expanded_tokens)

    def test_one_turn_can_keep_missed_deadline_and_payment_sections(self):
        ids = ["VTMS_114", "VTMS_118", "KARS_66", "VTMS_57", "VTMS_74", "VTMS_204"]
        laws = [
            self._law(law_id=law_id, domain=law_id.split("_", 1)[0])
            for law_id in ids
        ]
        service = QueryUnderstandingService(laws)

        result = service.analyze(
            "Rahatrahvi kaebetähtaeg on möödas. Kas saan tähtaja ennistada ja "
            "trahvi maksta osade kaupa?"
        )

        self.assertEqual(set(result.section_hints), set(ids))
        self.assertIn("tähtaja ennistamise taotlus", result.expanded_tokens)
        self.assertIn("rahatrahvi tasumine ositi", result.expanded_tokens)
        self.assertIn("KARS", result.domain_hints)
        self.assertIn("VTMS", result.domain_hints)

    def test_legal_search_uses_fuzzy_expansion_but_only_returns_real_law_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "laws.json"
            path.write_text(json.dumps([self._law()], ensure_ascii=False), encoding="utf-8")

            enabled = load_settings({
                "LEGAL_MIN_SCORE": "6",
                "LEGAL_RELATIVE_THRESHOLD": "0.6",
                "QUERY_UNDERSTANDING_ENABLED": "true",
                "QUERY_FUZZY_THRESHOLD": "0.82",
            })
            service = LegalSearchService(data_file=path, settings=enabled)
            laws, interpretation = service.search_laws_with_context(
                "Kas abipoliteinuku võib mind kinni pidada?", ""
            )

            self.assertEqual([law["id"] for law in laws], ["ABIPOLS_12"])
            self.assertIn("abipolitseiniku", interpretation.expanded_tokens)

            disabled = load_settings({
                "LEGAL_MIN_SCORE": "6",
                "LEGAL_RELATIVE_THRESHOLD": "0.6",
                "QUERY_UNDERSTANDING_ENABLED": "false",
            })
            old_style = LegalSearchService(data_file=path, settings=disabled)
            old_results = old_style.search_laws(
                "Kas abipoliteinuku võib mind kinni pidada?", ""
            )
            self.assertEqual(old_results, [])


if __name__ == "__main__":
    unittest.main()
