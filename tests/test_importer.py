import unittest
from unittest.mock import patch

import scripts.import_riigiteataja as importer
from scripts.import_riigiteataja import make_record, validate


class ImporterTests(unittest.TestCase):
    def test_make_record_canonicalizes_prefix(self):
        record = make_record(
            "AvTS",
            "Avaliku teabe seadus",
            "https://www.riigiteataja.ee/akt/AvTS",
            ("10", "Pealkiri", "Paragrahvi tekst"),
        )
        self.assertEqual(record["id"], "AVTS_10")
        self.assertEqual(record["domain"], "AVTS")

    def test_validate_rejects_empty_import(self):
        with self.assertRaises(ValueError):
            validate([])

    def test_find_current_act_id_accepts_globaal_id(self):
        search_payload = b"<response><item><globaalID>123456789012</globaalID></item></response>"
        act_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<oigusakt><pealkiri>Vola-oigusseadus</pealkiri><globaalID>123456789012</globaalID></oigusakt>'
        )

        with patch.object(importer, "cached_fetch", side_effect=[search_payload, act_xml]):
            act_id = importer.find_current_act_id("VOS", "Vola-oigusseadus")

        self.assertEqual(act_id, "123456789012")

    def test_find_current_act_id_accepts_json_globaal_id(self):
        search_payload = b'{"items":[{"globaalID":"123456789012"}]}'
        act_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<oigusakt><pealkiri>Testiseadus</pealkiri></oigusakt>'
        )

        with patch.object(importer, "cached_fetch", side_effect=[search_payload, act_xml]):
            act_id = importer.find_current_act_id("TEST", "Testiseadus")

        self.assertEqual(act_id, "123456789012")

    def test_find_current_act_id_uses_official_title_filter(self):
        search_payload = b"<response><item><globaalID>123456789012</globaalID></item></response>"
        act_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<oigusakt><aktinimi><nimi><pealkiri>Testiseadus</pealkiri></nimi></aktinimi></oigusakt>'
        )
        urls = []

        def fake_cached_fetch(url, force=False):
            urls.append(url)
            return search_payload if len(urls) == 1 else act_xml

        with patch.object(importer, "cached_fetch", side_effect=fake_cached_fetch):
            act_id = importer.find_current_act_id("TEST", "Testiseadus")

        self.assertEqual(act_id, "123456789012")
        self.assertIn("pealkiri=Testiseadus", urls[0])
        self.assertNotIn("otsing=", urls[0])

    def test_find_current_act_id_retries_title_without_conjunctions(self):
        empty_search = b"<response></response>"
        search_payload = b"<response><item><globaalID>123122022004</globaalID></item></response>"
        act_xml = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<oigusakt><aktinimi><nimi><pealkiri>"
            "Korteriomandi- ja korteriühistuseadus"
            "</pealkiri></nimi></aktinimi></oigusakt>"
        ).encode("utf-8")
        urls = []

        def fake_cached_fetch(url, force=False):
            urls.append((url, force))
            return [empty_search, search_payload, act_xml][len(urls) - 1]

        with patch.object(importer, "cached_fetch", side_effect=fake_cached_fetch):
            act_id = importer.find_current_act_id(
                "KRTS", "Korteriomandi- ja korteriühistuseadus"
            )

        self.assertEqual(act_id, "123122022004")
        self.assertIn("pealkiri=Korteriomandi+korteri%C3%BChistuseadus", urls[1][0])

    def test_find_current_act_id_refreshes_invalid_cached_xml_once(self):
        search_payload = b"<response><globaalID>123456789012</globaalID></response>"
        invalid_xml = b"<oigusakt>"
        act_xml = b"<oigusakt><pealkiri>Testiseadus</pealkiri></oigusakt>"
        calls = []

        def fake_cached_fetch(url, force=False):
            calls.append((url, force))
            return [search_payload, invalid_xml, act_xml][len(calls) - 1]

        with patch.object(importer, "cached_fetch", side_effect=fake_cached_fetch):
            act_id = importer.find_current_act_id("TEST", "Testiseadus")

        self.assertEqual(act_id, "123456789012")
        self.assertFalse(calls[1][1])
        self.assertTrue(calls[2][1])

    def test_extract_sections_reads_current_rt_paragrahv_nr_schema(self):
        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <oigusakt>
          <sisu>
            <paragrahv id='para1'>
              <paragrahvNr>1</paragrahvNr>
              <kuvatavNr>§ 1.</kuvatavNr>
              <paragrahviPealkiri>Reguleerimisala</paragrahviPealkiri>
              <loige><sisuTekst><tavatekst>Esimese paragrahvi tekst.</tavatekst></sisuTekst></loige>
            </paragrahv>
            <paragrahv id='para2'>
              <paragrahvNr>2</paragrahvNr>
              <kuvatavNr>§ 2.</kuvatavNr>
              <loige><sisuTekst><tavatekst>Teise paragrahvi tekst.</tavatekst></sisuTekst></loige>
            </paragrahv>
          </sisu>
        </oigusakt>""".encode("utf-8")
        sections = importer.extract_sections(importer.parse_xml(xml))
        self.assertEqual([row[0] for row in sections], ["1", "2"])
        self.assertIn("Esimese paragrahvi tekst", sections[0][2])

    def test_extract_sections_uses_rt_anchor_for_superscript_section(self):
        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <oigusakt><sisu>
          <paragrahv id='para3b2'>
            <paragrahvNr>32</paragrahvNr>
            <kuvatavNr>§ 3².</kuvatavNr>
            <loige><sisuTekst><tavatekst>Ülaindeksiga sätte tekst.</tavatekst></sisuTekst></loige>
          </paragrahv>
        </sisu></oigusakt>""".encode("utf-8")
        sections = importer.extract_sections(importer.parse_xml(xml))
        self.assertEqual(sections[0][0], "3B2")
        record = importer.make_record("VOS", "Võlaõigusseadus", "https://www.riigiteataja.ee/akt/123", sections[0])
        self.assertEqual(record["id"], "VOS_3B2")
        self.assertTrue(record["url"].endswith("#para3b2"))

    def test_extract_law_name_prefers_aktinimi_metadata(self):
        xml = """<oigusakt>
          <aktinimi><nimi><pealkiri>Õige seaduse nimi</pealkiri></nimi></aktinimi>
          <sisu><paragrahv id='para1'><pealkiri>Vale peatüki pealkiri</pealkiri></paragrahv></sisu>
        </oigusakt>""".encode("utf-8")
        name = importer.extract_law_name(importer.parse_xml(xml), "fallback")
        self.assertEqual(name, "Õige seaduse nimi")


if __name__ == "__main__":
    unittest.main()
