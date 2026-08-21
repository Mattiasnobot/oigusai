from __future__ import annotations

import hashlib
import unittest
import urllib.parse
from datetime import date

from services.rt_current_retrieval import VerifiedRTLiveRetrievalService
from services.rt_current_revision import RTCurrentRetrievalError, RTCurrentRevisionResolver
from services.rt_section_evidence import canonical_section, extract_section
from tests.rt_v114_fixtures import ACT_ID, TITLE, rt_xml, search_fetcher, search_payload, xml_fetcher

AS_OF = date(2026, 8, 21)


def _service(payload=None, factory=None):
    resolver = RTCurrentRevisionResolver(
        search_fetcher=search_fetcher(payload or search_payload(ACT_ID)),
        xml_fetcher=xml_fetcher(factory),
    )
    return VerifiedRTLiveRetrievalService(resolver=resolver)


class RTSectionRetrievalTests(unittest.TestCase):
    def test_section_identifiers_are_canonicalized(self):
        self.assertEqual(canonical_section("§ 95"), "95")
        self.assertEqual(canonical_section("3²"), "3B2")
        self.assertEqual(canonical_section("3^2"), "3B2")
        self.assertEqual(canonical_section("54a"), "54A")

    def test_exact_section_is_extracted(self):
        result = extract_section(rt_xml(), "95")
        self.assertEqual(result["section"], "95")
        self.assertEqual(result["heading"], "Kontrollsäte")
        self.assertIn("kirjalikku", result["text"])

    def test_superscript_anchor_is_supported(self):
        result = extract_section(rt_xml(section_number="3b2"), "3²")
        self.assertEqual(result["section"], "3B2")

    def test_missing_section_fails_closed(self):
        with self.assertRaises(RTCurrentRetrievalError):
            extract_section(rt_xml(), "999")

    def test_duplicate_section_with_different_text_fails_closed(self):
        data = rt_xml().replace(
            b"</sisu>",
            b'<paragrahv id="para95"><paragrahvNr>95</paragrahvNr><loige><sisuTekst>Teine vastuoluline tekst sama paragrahvi numbriga.</sisuTekst></loige></paragrahv></sisu>',
        )
        with self.assertRaises(RTCurrentRetrievalError):
            extract_section(data, "95")

    def test_verified_section_record_keeps_model_context_disabled(self):
        result = _service().retrieve_sections(TITLE, ["95"], as_of=AS_OF, domain="TLS")[0]
        self.assertEqual(result["id"], "TLS_95")
        self.assertEqual(result["verification_status"], "BINDING_SECTION_VERIFIED")
        self.assertEqual(result["evidence_source"], "rt_live_verified")
        self.assertEqual(result["source_id"], "RT_NATIONAL_LAW")
        self.assertEqual(result["content_hash"], hashlib.sha256(result["text"].encode()).hexdigest())
        self.assertFalse(result["model_context_enabled"])
        self.assertFalse(result["corpus_write_enabled"])

    def test_section_provenance_changes_with_exact_section_text(self):
        first = _service().retrieve_sections(TITLE, ["95"], as_of=AS_OF, domain="TLS")[0]
        second = _service(factory=lambda act_id: rt_xml(
            act_id,
            section_text="Muudetud, kuid endiselt piisavalt pikk kontrolltekst paragrahvi jaoks.",
        )).retrieve_sections(TITLE, ["95"], as_of=AS_OF, domain="TLS")[0]
        self.assertNotEqual(first["section_provenance_sha256"], second["section_provenance_sha256"])

    def test_upgrade_replaces_matching_local_candidate(self):
        local = [{
            "id": "TLS_95", "title": "old", "text": "old", "source": "Riigi Teataja: TLS",
            "domain": "TLS", "law_name": TITLE, "section": "95", "aliases": ["vorm"],
            "url": "https://www.riigiteataja.ee/akt/old", "content_hash": hashlib.sha256(b"old").hexdigest(),
        }]
        result = _service().upgrade_candidates(local, as_of=AS_OF)
        self.assertEqual(result["status"], "LIVE_VERIFIED")
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["fallback_count"], 0)
        self.assertEqual(result["laws"][0]["evidence_source"], "rt_live_verified")

    def test_live_failure_keeps_explicit_local_corpus_fallback(self):
        local = [{
            "id": "TLS_95", "title": "old", "text": "old", "source": "Riigi Teataja: TLS",
            "domain": "TLS", "law_name": TITLE, "section": "95", "aliases": [],
            "url": "https://www.riigiteataja.ee/akt/old", "content_hash": hashlib.sha256(b"old").hexdigest(),
        }]
        result = _service(payload=b"<tulemused/>").upgrade_candidates(local, as_of=AS_OF)
        self.assertEqual(result["status"], "LOCAL_CORPUS_FALLBACK")
        self.assertEqual(result["verified_count"], 0)
        self.assertEqual(result["laws"][0]["verification_status"], "LOCAL_CORPUS_FALLBACK")
        self.assertFalse(result["laws"][0]["model_context_enabled"])

    def test_partial_live_failure_is_not_mislabeled_as_fully_verified(self):
        def search(url, timeout, user_agent):
            title = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["pealkiri"][0]
            return (search_payload(ACT_ID) if title == TITLE else b"<tulemused/>", url)

        service = VerifiedRTLiveRetrievalService(resolver=RTCurrentRevisionResolver(
            search_fetcher=search,
            xml_fetcher=xml_fetcher(),
        ))
        local = [
            {"id": "TLS_95", "title": "x", "text": "x", "source": "x", "domain": "TLS", "law_name": TITLE, "section": "95", "aliases": [], "url": "https://www.riigiteataja.ee/akt/x", "content_hash": hashlib.sha256(b"x").hexdigest()},
            {"id": "VOS_308", "title": "y", "text": "y", "source": "y", "domain": "VOS", "law_name": "Võlaõigusseadus", "section": "308", "aliases": [], "url": "https://www.riigiteataja.ee/akt/y", "content_hash": hashlib.sha256(b"y").hexdigest()},
        ]
        result = service.upgrade_candidates(local, as_of=AS_OF)
        self.assertEqual(result["status"], "PARTIAL_LIVE_FALLBACK")
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["fallback_count"], 1)
        self.assertEqual(result["laws"][1]["verification_status"], "LOCAL_CORPUS_FALLBACK")


if __name__ == "__main__":
    unittest.main()
