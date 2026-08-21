from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from services.rt_authority import (
    RTAuthorityError,
    extract_revision_metadata,
    verify_live_rt_binding_authority,
)
from services.rt_live_source import verify_live_rt_source


ACT_ID = "106032026003"
AS_OF = date(2026, 8, 21)


def _xml(
    *,
    issuer: str = "Riigikogu",
    act_type: str = "seadus",
    text_type: str = "terviktekst",
    valid_from: str = "2026-03-07",
    valid_to: str = "",
    publication_marker: str = "RT I, 06.03.2026, 3",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <metaandmed>
    <globaalID>{ACT_ID}</globaalID>
    <aktinimi>Avaliku teabe seadus</aktinimi>
    <valjaandja>{issuer}</valjaandja>
    <aktiLiik>{act_type}</aktiLiik>
    <tekstiLiik>{text_type}</tekstiLiik>
    <kehtivuseAlgus>{valid_from}</kehtivuseAlgus>
    <kehtivuseLopp>{valid_to}</kehtivuseLopp>
    <avaldamismarge>{publication_marker}</avaldamismarge>
  </metaandmed>
  <sisu><paragrahv><lause>See on piisavalt pikk kontrolltekst ametliku redaktsiooni authority ja currentness verifitseerimise testimiseks.</lause></paragrahv></sisu>
</akt>
""".encode("utf-8")


def _fetcher(data: bytes):
    def fetch(url, timeout, user_agent):
        return data, url

    return fetch


class _RejectingRegistry:
    def supports_claim(self, source_id, claim_class):
        return False

    def source(self, source_id):
        return {}

    def validates_url(self, source_id, url):
        return False


class RTAuthorityTests(unittest.TestCase):
    def test_current_national_law_is_promoted(self):
        result = verify_live_rt_binding_authority(ACT_ID, as_of=AS_OF, fetcher=_fetcher(_xml()))
        self.assertEqual(result["status"], "BINDING_SOURCE_VERIFIED")
        self.assertEqual(result["source_id"], "RT_NATIONAL_LAW")
        self.assertEqual(result["authority_class"], "binding_national_law")
        self.assertTrue(result["authority_verified"])
        self.assertTrue(result["currentness_verified"])
        self.assertEqual(result["claim_class"], "binding_rule")

    def test_current_national_regulation_is_promoted(self):
        result = verify_live_rt_binding_authority(
            ACT_ID,
            as_of=AS_OF,
            fetcher=_fetcher(_xml(issuer="Vabariigi Valitsus", act_type="määrus")),
        )
        self.assertEqual(result["source_id"], "RT_NATIONAL_LAW")

    def test_current_local_regulation_is_promoted(self):
        result = verify_live_rt_binding_authority(
            ACT_ID,
            as_of=AS_OF,
            fetcher=_fetcher(
                _xml(
                    issuer="Kehtna Vallavolikogu",
                    act_type="määrus",
                    publication_marker="RT IV, 07.05.2026, 46",
                )
            ),
        )
        self.assertEqual(result["source_id"], "RT_LOCAL_LAW")
        self.assertEqual(result["authority_class"], "binding_local_law")

    def test_rt_iv_decision_cannot_masquerade_as_local_binding_rule(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(act_type="otsus", publication_marker="RT IV, 07.05.2026, 46")),
            )

    def test_rt_iii_order_cannot_masquerade_as_national_binding_rule(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(act_type="korraldus", publication_marker="RT III, 12.06.2026, 3")),
            )

    def test_rt_ii_document_is_not_silently_promoted(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(act_type="seadus", publication_marker="RT II, 12.06.2026, 3")),
            )

    def test_expired_revision_is_rejected(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(valid_to="2026-08-01")),
            )

    def test_valid_to_boundary_is_exclusive(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(valid_to="2026-08-21")),
            )

    def test_revision_not_yet_in_force_is_rejected(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(valid_from="2026-09-01")),
            )

    def test_missing_validity_field_fails_closed(self):
        data = _xml().replace(b"    <kehtivuseLopp></kehtivuseLopp>\n", b"")
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(ACT_ID, as_of=AS_OF, fetcher=_fetcher(data))

    def test_malformed_validity_date_fails_closed(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml(valid_from="07/03/2026")),
            )

    def test_future_date_assertion_is_disabled(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=date(2099, 1, 1),
                fetcher=_fetcher(_xml()),
            )

    def test_metadata_attributes_are_supported_without_body_inference(self):
        data = f"""<akt><meta globaalID="{ACT_ID}" valjaandja="Riigikogu" aktiLiik="seadus" tekstiliik="terviktekst" kehtivuseAlgus="2026-03-07" kehtivuseLopp="" avaldamismarge="RT I, 06.03.2026, 3"/><aktinimi>Avaliku teabe seadus</aktinimi><sisu>piisavalt pikk kontrolltekst authority parserile</sisu></akt>""".encode("utf-8")
        metadata = extract_revision_metadata(data)
        self.assertEqual(metadata["issuer"], "Riigikogu")
        self.assertEqual(metadata["act_type"], "seadus")
        self.assertIn("valid_to", metadata)

    def test_registry_must_authorize_binding_claim_class(self):
        with self.assertRaises(RTAuthorityError):
            verify_live_rt_binding_authority(
                ACT_ID,
                as_of=AS_OF,
                fetcher=_fetcher(_xml()),
                registry=_RejectingRegistry(),
            )

    def test_revision_provenance_changes_when_audited_metadata_changes(self):
        first = verify_live_rt_binding_authority(ACT_ID, as_of=AS_OF, fetcher=_fetcher(_xml()))
        second = verify_live_rt_binding_authority(
            ACT_ID,
            as_of=AS_OF,
            fetcher=_fetcher(_xml(text_type="terviktekst-parandatud")),
        )
        self.assertNotEqual(first["revision_provenance_sha256"], second["revision_provenance_sha256"])

    def test_v11_2_1_exact_source_layer_remains_unasserted(self):
        result = verify_live_rt_source(ACT_ID, fetcher=_fetcher(_xml()))
        self.assertEqual(result["authority_class"], "not_asserted")
        self.assertFalse(result["currentness_verified"])

    def test_committed_manifest_enables_only_audited_v11_3_layer(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "data/rt_authority_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "V11.3-rt-authority-currentness-1")
        self.assertTrue(manifest["authority_classification_enabled"])
        self.assertTrue(manifest["currentness_verification_enabled"])
        self.assertTrue(manifest["binding_claim_policy_gate_enabled"])
        self.assertFalse(manifest["current_revision_resolution_enabled"])
        self.assertFalse(manifest["future_date_assertions_enabled"])
        for key in (
            "writes_legal_corpus",
            "writes_case_law_corpus",
            "retrieval_integration_enabled",
            "model_context_integration_enabled",
        ):
            self.assertFalse(manifest[key])

        previous = json.loads((root / "data/rt_live_adapter_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(previous["authority_classification_enabled"])
        self.assertFalse(previous["current_revision_resolution_enabled"])


if __name__ == "__main__":
    unittest.main()
