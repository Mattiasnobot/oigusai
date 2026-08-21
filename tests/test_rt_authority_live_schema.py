from __future__ import annotations

import unittest

from services.rt_authority import extract_revision_metadata


class RTAuthorityLiveSchemaTests(unittest.TestCase):
    def test_real_rt_akt_liik_element_name_is_recognized(self):
        xml = b"""<akt><metaandmed><aktLiik>seadus</aktLiik></metaandmed></akt>"""
        metadata = extract_revision_metadata(xml)
        self.assertEqual(metadata.get("act_type"), "seadus")

    def test_legacy_audited_akti_liik_alias_remains_supported(self):
        xml = b"""<akt><metaandmed><aktiLiik>m\xc3\xa4\xc3\xa4rus</aktiLiik></metaandmed></akt>"""
        metadata = extract_revision_metadata(xml)
        self.assertEqual(metadata.get("act_type"), "m\u00e4\u00e4rus")


if __name__ == "__main__":
    unittest.main()
