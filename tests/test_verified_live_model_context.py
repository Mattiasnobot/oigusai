from __future__ import annotations

import hashlib
import unittest
from datetime import date
from unittest.mock import patch

from config import load_settings
from services.offline_ai import OfflineAIService
from services.rt_current_retrieval import RT_CURRENT_RETRIEVAL_VERSION
from services.rt_section_evidence import compute_section_provenance_sha256
from services.verified_live_ai import (
    LiveModelContextAdmissionError,
    VerifiedLiveModelContextGate,
    VerifiedLiveModelContextService,
    VerifiedLiveOfflineAIService,
)


AS_OF = date(2026, 8, 21)


def local_record(record_id: str = "TLS_95", text: str = "Kohalik auditeeritud tekst.") -> dict:
    return {
        "id": record_id,
        "title": "Töölepingu seadus § 95",
        "text": text,
        "source": "Riigi Teataja: TLS",
        "domain": "TLS",
        "law_name": "Töölepingu seadus",
        "section": "95",
        "aliases": [],
        "url": "https://www.riigiteataja.ee/akt/103072026034#para95",
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def live_record(record_id: str = "TLS_95", text: str = "Töölepingu ülesütlemisavaldus tuleb teha kirjalikku taasesitamist võimaldavas vormis.") -> dict:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    revision_hash = "a" * 64
    section = "95"
    act_id = "103072026034"
    section_hash = compute_section_provenance_sha256({
        "version": RT_CURRENT_RETRIEVAL_VERSION,
        "act_id": act_id,
        "revision_provenance_sha256": revision_hash,
        "section": section,
        "content_hash": content_hash,
    })
    return {
        "id": record_id,
        "title": "Töölepingu seadus § 95",
        "text": text,
        "source": "Riigi Teataja live verified: Töölepingu seadus",
        "domain": "TLS",
        "law_name": "Töölepingu seadus",
        "section": section,
        "aliases": [],
        "url": f"https://www.riigiteataja.ee/akt/{act_id}#para95",
        "content_hash": content_hash,
        "evidence_source": "rt_live_verified",
        "verification_status": "BINDING_SECTION_VERIFIED",
        "source_id": "RT_NATIONAL_LAW",
        "authority_class": "binding_national_law",
        "authority_verified": True,
        "currentness_verified": True,
        "as_of_date": AS_OF.isoformat(),
        "act_id": act_id,
        "canonical_url": f"https://www.riigiteataja.ee/akt/{act_id}",
        "xml_url": f"https://www.riigiteataja.ee/public-api/api/v1/akt/{act_id}/xml",
        "revision_provenance_sha256": revision_hash,
        "section_provenance_sha256": section_hash,
        "xml_sha256": "b" * 64,
        "model_context_enabled": False,
        "corpus_write_enabled": False,
    }


class FakeLiveRetrieval:
    def __init__(self, laws):
        self.laws = list(laws)

    def upgrade_candidates(self, laws, *, as_of):
        return {
            "laws": [dict(item) for item in self.laws],
            "failures": [],
        }


class FakeContextService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def upgrade_for_model(self, laws, event_date=""):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class VerifiedLiveModelContextTests(unittest.TestCase):
    def test_config_defaults_disabled_and_can_be_enabled(self):
        self.assertFalse(load_settings({}).rt_verified_live_model_context_enabled)
        self.assertTrue(
            load_settings({"RT_VERIFIED_LIVE_MODEL_CONTEXT_ENABLED": "true"})
            .rt_verified_live_model_context_enabled
        )

    def test_exact_v11_4_live_section_is_admitted(self):
        result = VerifiedLiveModelContextGate().admit_live(live_record(), as_of=AS_OF)
        self.assertTrue(result["model_context_enabled"])
        self.assertEqual(result["model_context_admission"], "VERIFIED_LIVE_ADMITTED")
        self.assertEqual(result["model_context_source"], "rt_live_verified")

    def test_local_fallback_cannot_masquerade_as_live(self):
        record = live_record()
        record["verification_status"] = "LOCAL_CORPUS_FALLBACK"
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_pre_enabled_live_context_is_rejected(self):
        record = live_record()
        record["model_context_enabled"] = True
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_authority_class_mismatch_is_rejected(self):
        record = live_record()
        record["authority_class"] = "binding_local_law"
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_requested_date_mismatch_is_rejected(self):
        record = live_record()
        record["as_of_date"] = "2026-08-20"
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_non_official_canonical_url_is_rejected(self):
        record = live_record()
        record["canonical_url"] = "https://example.com/akt/103072026034"
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_tampered_section_text_is_rejected_even_with_hash_shaped_metadata(self):
        record = live_record()
        record["text"] += " Muudetud."
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_tampered_section_provenance_is_rejected(self):
        record = live_record()
        record["section_provenance_sha256"] = "c" * 64
        with self.assertRaises(LiveModelContextAdmissionError):
            VerifiedLiveModelContextGate().admit_live(record, as_of=AS_OF)

    def test_full_live_upgrade_builds_only_verified_live_model_context(self):
        service = VerifiedLiveModelContextService(
            live_retrieval=FakeLiveRetrieval([live_record()])
        )
        result = service.upgrade_for_model([local_record()], AS_OF.isoformat())
        self.assertEqual(result["status"], "LIVE_MODEL_CONTEXT")
        self.assertEqual(result["live_admitted_count"], 1)
        self.assertEqual(result["local_context_count"], 0)
        self.assertEqual(result["laws"][0]["model_context_admission"], "VERIFIED_LIVE_ADMITTED")

    def test_tampered_live_record_falls_back_to_original_local_candidate(self):
        tampered = live_record()
        tampered["text"] += " Muudetud."
        original = local_record(text="Algne lokaalne tekst.")
        service = VerifiedLiveModelContextService(
            live_retrieval=FakeLiveRetrieval([tampered])
        )
        result = service.upgrade_for_model([original], AS_OF.isoformat())
        self.assertEqual(result["status"], "LOCAL_MODEL_CONTEXT")
        self.assertEqual(result["laws"][0]["text"], "Algne lokaalne tekst.")
        self.assertEqual(result["laws"][0]["model_context_source"], "audited_local_corpus")
        self.assertEqual(len(result["admission_failures"]), 1)

    def test_partial_live_result_remains_explicitly_mixed(self):
        second_local = local_record("TLS_96", "Teine lokaalne tekst.")
        second_local.update({"section": "96", "title": "Töölepingu seadus § 96"})
        second_fallback = dict(second_local)
        second_fallback["verification_status"] = "LOCAL_CORPUS_FALLBACK"
        service = VerifiedLiveModelContextService(
            live_retrieval=FakeLiveRetrieval([live_record(), second_fallback])
        )
        result = service.upgrade_for_model([local_record(), second_local], AS_OF.isoformat())
        self.assertEqual(result["status"], "MIXED_MODEL_CONTEXT")
        self.assertEqual(result["live_admitted_count"], 1)
        self.assertEqual(result["local_context_count"], 1)
        self.assertEqual(result["laws"][1]["model_context_source"], "audited_local_corpus")

    def test_disabled_wrapper_does_not_touch_live_context_service(self):
        fake = FakeContextService(error=AssertionError("must not be called"))
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=False,
            live_context_service=fake,
        )
        laws = [local_record()]
        with patch.object(OfflineAIService, "analyze_case_structured", return_value=("ok", False, [])):
            result = service.analyze_case_structured("case", laws, AS_OF.isoformat(), [])
        self.assertEqual(result, ("ok", False, []))
        self.assertEqual(fake.calls, 0)
        self.assertNotIn("model_context_admission", laws[0])

    def test_enabled_wrapper_mutates_same_law_list_before_parent_model_call(self):
        admitted = live_record()
        admitted["model_context_enabled"] = True
        admitted["model_context_admission"] = "VERIFIED_LIVE_ADMITTED"
        admitted["model_context_source"] = "rt_live_verified"
        fake = FakeContextService(result={
            "laws": [admitted],
            "status": "LIVE_MODEL_CONTEXT",
            "live_admitted_count": 1,
            "local_context_count": 0,
            "model_context_enabled": True,
        })
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_service=fake,
        )
        laws = [local_record()]
        original_identity = id(laws)
        with patch.object(OfflineAIService, "analyze_case_structured", return_value=("ok", False, [])):
            service.analyze_case_structured("case", laws, AS_OF.isoformat(), [])
        self.assertEqual(id(laws), original_identity)
        self.assertEqual(laws[0]["verification_status"], "BINDING_SECTION_VERIFIED")
        self.assertEqual(service.last_live_model_context["status"], "LIVE_MODEL_CONTEXT")

    def test_wrapper_failure_keeps_original_audited_local_list(self):
        fake = FakeContextService(error=RuntimeError("RT offline"))
        service = VerifiedLiveOfflineAIService(
            settings=load_settings({}),
            live_model_context_enabled=True,
            live_context_service=fake,
        )
        laws = [local_record(text="Turvaline lokaalne tekst.")]
        with patch.object(OfflineAIService, "analyze_case_structured", return_value=("ok", False, [])):
            service.analyze_case_structured("case", laws, AS_OF.isoformat(), [])
        self.assertEqual(laws[0]["text"], "Turvaline lokaalne tekst.")
        self.assertEqual(service.last_live_model_context["status"], "LOCAL_MODEL_CONTEXT")


if __name__ == "__main__":
    unittest.main()
