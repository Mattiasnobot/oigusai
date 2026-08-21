from __future__ import annotations

import unittest

from services.rt_authority import extract_revision_metadata


class RTAuthorityDocumentSchemaTests(unittest.TestCase):
    def test_official_rt_dokument_liik_element_is_recognized(self):
        xml = b"""<akt><metaandmed><dokumentLiik>seadus</dokumentLiik></metaandmed></akt>"""
        metadata = extract_revision_metadata(xml)
        self.assertEqual(metadata.get("act_type"), "seadus")

    def test_dokument_liik_attribute_is_recognized(self):
        xml = b"""<akt><metaandmed dokumentLiik="seadus"/></akt>"""
        metadata = extract_revision_metadata(xml)
        self.assertEqual(metadata.get("act_type"), "seadus")


if __name__ == "__main__":
    unittest.main()
