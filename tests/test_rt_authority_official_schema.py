from __future__ import annotations

import unittest
from datetime import date

from services.rt_authority import extract_revision_metadata, verify_live_rt_binding_authority


ACT_ID = "106032026003"
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <metaandmed>
    <globaalID>{ACT_ID}</globaalID>
    <aktinimi>Avaliku teabe seadus</aktinimi>
    <valjaandja>Riigikogu</valjaandja>
    <dokumentLiik>seadus</dokumentLiik>
    <tekstiliik>terviktekst</tekstiliik>
    <kehtivus>
      <kehtivuseAlgus>2026-03-07</kehtivuseAlgus>
      <kehtivuseLopp></kehtivuseLopp>
    </kehtivus>
    <avaldamismarge>
      <RTosa>I</RTosa>
      <avaldamineKuupaev>2026-03-06</avaldamineKuupaev>
      <RTartikkel>3</RTartikkel>
    </avaldamismarge>
  </metaandmed>
  <sisu><paragrahv><lause>See on piisavalt pikk kontrolltekst ametliku Riigi Teataja struktureeritud XML metaandmete verifitseerimise regressioonitestiks.</lause></paragrahv></sisu>
</akt>
""".encode("utf-8")


def _fetcher(url, timeout, user_agent):
    return XML, url


class RTAuthorityOfficialSchemaTests(unittest.TestCase):
    def test_official_structured_metadata_fields_are_extracted(self):
        metadata = extract_revision_metadata(XML)
        self.assertEqual(metadata["act_type"], "seadus")
        self.assertEqual(metadata["publication_series"], "I")
        self.assertEqual(metadata["publication_date"], "2026-03-06")
        self.assertEqual(metadata["publication_article"], "3")
        self.assertEqual(metadata["valid_from"], "2026-03-07")
        self.assertIn("valid_to", metadata)

    def test_official_structured_metadata_can_support_binding_source(self):
        result = verify_live_rt_binding_authority(
            ACT_ID,
            as_of=date(2026, 8, 21),
            fetcher=_fetcher,
        )
        self.assertEqual(result["status"], "BINDING_SOURCE_VERIFIED")
        self.assertEqual(result["source_id"], "RT_NATIONAL_LAW")
        self.assertEqual(result["authority_class"], "binding_national_law")
        self.assertEqual(result["act_type"], "seadus")
        self.assertEqual(result["publication_series"], "RT I")
        self.assertEqual(result["publication_date"], "2026-03-06")
        self.assertEqual(result["publication_article"], "3")
        self.assertTrue(result["authority_verified"])
        self.assertTrue(result["currentness_verified"])
        self.assertFalse(result["retrieval_enabled"])
        self.assertFalse(result["model_context_enabled"])


if __name__ == "__main__":
    unittest.main()
