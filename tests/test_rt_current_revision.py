from __future__ import annotations

import unittest
import urllib.parse
from datetime import date

from services.rt_current_revision import (
    RTCurrentRetrievalError,
    RTCurrentRevisionResolver,
    build_search_url,
    extract_candidate_ids,
    extract_official_title,
)
from tests.rt_v114_fixtures import ACT_ID, TITLE, rt_xml, search_fetcher, search_payload, xml_fetcher

AS_OF = date(2026, 8, 21)


class RTCurrentRevisionResolverTests(unittest.TestCase):
    def test_search_url_pins_currentness_filters_and_date(self):
        url = build_search_url(TITLE, as_of=AS_OF, document_type="seadus")
        parsed = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/oigusakt_otsing/1/otsi")
        self.assertEqual(params["kehtiv"], ["2026-08-21"])
        self.assertEqual(params["kehtivKehtetus"], ["false"])
        self.assertEqual(params["mitteJoustunud"], ["false"])
        self.assertEqual(params["pealkiri"], [TITLE])

    def test_future_date_fails_closed(self):
        with self.assertRaises(RTCurrentRetrievalError):
            build_search_url(TITLE, as_of=date(2099, 1, 1), document_type="seadus")

    def test_non_binding_document_type_is_not_a_search_class(self):
        with self.assertRaises(RTCurrentRetrievalError):
            build_search_url(TITLE, as_of=AS_OF, document_type="otsus")

    def test_candidate_ids_support_structured_and_url_shapes(self):
        payload = f'<globaalID>{ACT_ID}</globaalID>{{"aktId":"{ACT_ID}"}} /akt/131032026006'.encode()
        self.assertEqual(extract_candidate_ids(payload), [ACT_ID, "131032026006"])

    def test_title_is_taken_from_aktinimi_not_section_heading(self):
        data = rt_xml().replace(b"Kontrolls\xc3\xa4te", b"Vale peat\xc3\xbckk")
        self.assertEqual(extract_official_title(data), TITLE)

    def test_exact_current_revision_passes_v11_3_gate(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(search_payload(ACT_ID)),
            xml_fetcher=xml_fetcher(),
        )
        result = resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))
        self.assertEqual(result.binding["status"], "BINDING_SOURCE_VERIFIED")
        self.assertEqual(result.binding["source_id"], "RT_NATIONAL_LAW")
        self.assertEqual(result.binding["act_id"], ACT_ID)
        self.assertEqual(result.official_title, TITLE)

    def test_wrong_exact_title_is_rejected(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(search_payload(ACT_ID)),
            xml_fetcher=xml_fetcher(lambda act_id: rt_xml(act_id, title="Avaliku teenistuse seadus")),
        )
        with self.assertRaises(RTCurrentRetrievalError):
            resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))

    def test_zero_search_candidates_fail_closed(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(b"<tulemused/>"),
            xml_fetcher=xml_fetcher(),
        )
        with self.assertRaises(RTCurrentRetrievalError):
            resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))

    def test_search_redirect_cannot_leave_rt_host(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(
                search_payload(ACT_ID),
                final_url_override="https://example.com/api/oigusakt_otsing/1/otsi",
            ),
            xml_fetcher=xml_fetcher(),
        )
        with self.assertRaises(RTCurrentRetrievalError):
            resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))

    def test_multiple_exact_current_revisions_are_ambiguous(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(search_payload(ACT_ID, "131032026006")),
            xml_fetcher=xml_fetcher(lambda act_id: rt_xml(act_id)),
        )
        with self.assertRaises(RTCurrentRetrievalError):
            resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))

    def test_rt_iii_candidate_cannot_be_promoted(self):
        resolver = RTCurrentRevisionResolver(
            search_fetcher=search_fetcher(search_payload(ACT_ID)),
            xml_fetcher=xml_fetcher(lambda act_id: rt_xml(act_id, rt_part="III")),
        )
        with self.assertRaises(RTCurrentRetrievalError):
            resolver.resolve(TITLE, as_of=AS_OF, document_types=("seadus",))


if __name__ == "__main__":
    unittest.main()
