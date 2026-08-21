from __future__ import annotations

import json
import unittest
from pathlib import Path

from services.rt_live_source import (
    RTLiveSourceError,
    canonical_act_url,
    extract_act_id,
    verify_live_rt_source,
    xml_api_url,
)


ACT_ID = "106032026003"
XML = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<akt>
  <metaandmed>
    <globaalID>{ACT_ID}</globaalID>
    <aktinimi>Avaliku teabe seadus</aktinimi>
  </metaandmed>
  <sisu><paragrahv><lause>See on piisavalt pikk kontrolltekst ametliku XML allika verifitseerimise testimiseks.</lause></paragrahv></sisu>
</akt>
""".encode("utf-8")


class RTLiveSourceTests(unittest.TestCase):
    def test_numeric_id_builds_canonical_urls(self):
        self.assertEqual(extract_act_id(ACT_ID), ACT_ID)
        self.assertEqual(canonical_act_url(ACT_ID), f"https://www.riigiteataja.ee/akt/{ACT_ID}")
        self.assertEqual(xml_api_url(ACT_ID), f"https://www.riigiteataja.ee/public-api/api/v1/akt/{ACT_ID}/xml")

    def test_exact_browser_and_xml_urls_are_accepted(self):
        self.assertEqual(extract_act_id(f"https://www.riigiteataja.ee/akt/{ACT_ID}"), ACT_ID)
        self.assertEqual(extract_act_id(f"https://riigiteataja.ee/et/akt/{ACT_ID}"), ACT_ID)
        self.assertEqual(extract_act_id(xml_api_url(ACT_ID)), ACT_ID)

    def test_committed_manifest_keeps_every_integration_disabled(self):
        manifest = json.loads((Path(__file__).resolve().parent.parent / "data/rt_live_adapter_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["live_adapter_available"])
        self.assertTrue(manifest["network_on_demand_only"])
        for key in (
            "authority_classification_enabled",
            "current_revision_resolution_enabled",
            "writes_legal_corpus",
            "writes_case_law_corpus",
            "retrieval_integration_enabled",
            "model_context_integration_enabled",
        ):
            self.assertFalse(manifest[key])

    def test_non_official_host_is_rejected(self):
        with self.assertRaises(RTLiveSourceError):
            extract_act_id(f"https://example.com/akt/{ACT_ID}")

    def test_ambiguous_query_or_fragment_is_rejected(self):
        for suffix in ("?leiaKehtiv", "#para1"):
            with self.subTest(suffix=suffix), self.assertRaises(RTLiveSourceError):
                extract_act_id(f"https://www.riigiteataja.ee/akt/{ACT_ID}{suffix}")

    def test_verified_source_keeps_authority_and_currentness_unasserted(self):
        def fetcher(url, timeout, user_agent):
            return XML, url

        result = verify_live_rt_source(ACT_ID, fetcher=fetcher)
        self.assertEqual(result["status"], "OFFICIAL_SOURCE_VERIFIED")
        self.assertEqual(result["title"], "Avaliku teabe seadus")
        self.assertEqual(result["authority_class"], "not_asserted")
        self.assertFalse(result["currentness_verified"])
        self.assertFalse(result["retrieval_enabled"])
        self.assertFalse(result["model_context_enabled"])
        self.assertFalse(result["corpus_write_enabled"])
        self.assertEqual(len(result["xml_sha256"]), 64)
        self.assertEqual(len(result["text_sha256"]), 64)

    def test_fetch_to_different_act_id_fails_closed(self):
        def fetcher(url, timeout, user_agent):
            return XML, "https://www.riigiteataja.ee/public-api/api/v1/akt/111042025003/xml"

        with self.assertRaises(RTLiveSourceError):
            verify_live_rt_source(ACT_ID, fetcher=fetcher)

    def test_xml_metadata_id_must_match_request(self):
        wrong = XML.replace(ACT_ID.encode(), b"111042025003", 1)

        def fetcher(url, timeout, user_agent):
            return wrong, url

        with self.assertRaises(RTLiveSourceError):
            verify_live_rt_source(ACT_ID, fetcher=fetcher)

    def test_malformed_xml_fails_closed(self):
        def fetcher(url, timeout, user_agent):
            return b"<not-closed>" * 20, url

        with self.assertRaises(RTLiveSourceError):
            verify_live_rt_source(ACT_ID, fetcher=fetcher)

    def test_dtd_and_entity_declarations_are_rejected(self):
        malicious = b"<!DOCTYPE akt [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>" + XML

        def fetcher(url, timeout, user_agent):
            return malicious, url

        with self.assertRaises(RTLiveSourceError):
            verify_live_rt_source(ACT_ID, fetcher=fetcher)


if __name__ == "__main__":
    unittest.main()
