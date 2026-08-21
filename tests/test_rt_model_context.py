from __future__ import annotations

import hashlib
import unittest
from datetime import date

from services.rt_current_retrieval import RT_CURRENT_RETRIEVAL_VERSION
from services.rt_model_context import (
    RTModelContextError,
    admit_model_context,
    validate_verified_live_record,
)
from services.rt_section_evidence import compute_section_provenance_sha256


def live_record(*, section="95", text="Kontrollitud õigusnormi tekst.") -> dict:
    act_id = "103072026034"
    revision_hash = "1" * 64
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    provenance = compute_section_provenance_sha256({
        "version": RT_CURRENT_RETRIEVAL_VERSION,
        "act_id": act_id,
        "revision_provenance_sha256": revision_hash,
        "section": section,
        "content_hash": content_hash,
    })
    return {
        "id": f"TLS_{section}",
        "title": f"Töölepingu seadus § {section}",
        "text": text,
        "source": "Riigi Teataja live verified: Töölepingu seadus",
        "domain": "TLS",
        "law_name": "Töölepingu seadus",
        "section": section,
        "aliases": [],
        "url": f"https://www.riigiteataja.ee/akt/{act_id}#para{section.casefold()}",
        "content_hash": content_hash,
        "evidence_source": "rt_live_verified",
        "verification_status": "BINDING_SECTION_VERIFIED",
        "source_id": "RT_NATIONAL_LAW",
        "authority_class": "binding_national_law",
        "authority_verified": True,
        "currentness_verified": True,
        "as_of_date": "2026-08-21",
        "act_id": act_id,
        "canonical_url": f"https://www.riigiteataja.ee/akt/{act_id}",
        "xml_url": f"https://www.riigiteataja.ee/public-api/api/v1/akt/{act_id}/xml",
        "revision_provenance_sha256": revision_hash,
        "section_provenance_sha256": provenance,
        "xml_sha256": "2" * 64,
        "model_context_enabled": False,
        "corpus_write_enabled": False,
    }


def local_candidate() -> dict:
    text = "Auditeeritud kohaliku korpuse tekst."
    return {
        "id": "TLS_95",
        "title": "Töölepingu seadus § 95",
        "text": text,
        "source": "Riigi Teataja: TLS",
        "domain": "TLS",
        "law_name": "Töölepingu seadus",
        "section": "95",
        "aliases": [],
        "url": "https://www.riigiteataja.ee/akt/TLS?leiaKehtiv#para95",
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def local_fallback(candidate=None) -> dict:
    result = dict(candidate or local_candidate())
    result["evidence_source"] = "audited_local_corpus"
    result["verification_status"] = "LOCAL_CORPUS_FALLBACK"
    result["model_context_enabled"] = False
    return result


class RTModelContextGateTests(unittest.TestCase):
    def test_verified_live_binding_section_is_admitted(self):
        admitted = admit_model_context(
            [live_record()],
            expected_as_of=date(2026, 8, 21),
        )
        self.assertEqual(admitted["status"], "VERIFIED_LIVE_CONTEXT")
        self.assertEqual(admitted["live_count"], 1)
        self.assertTrue(admitted["laws"][0]["model_context_enabled"])
        self.assertEqual(
            admitted["laws"][0]["model_context_admission"],
            "VERIFIED_LIVE_BINDING_SECTION",
        )

    def test_tampered_live_text_fails_closed(self):
        record = live_record()
        record["text"] += " muudetud"
        with self.assertRaises(RTModelContextError):
            validate_verified_live_record(record, expected_as_of=date(2026, 8, 21))

    def test_tampered_section_provenance_fails_closed(self):
        record = live_record()
        record["section_provenance_sha256"] = "f" * 64
        with self.assertRaises(RTModelContextError):
            validate_verified_live_record(record, expected_as_of=date(2026, 8, 21))

    def test_wrong_authority_mapping_fails_closed(self):
        record = live_record()
        record["authority_class"] = "regulator_guidance"
        with self.assertRaises(RTModelContextError):
            validate_verified_live_record(record, expected_as_of=date(2026, 8, 21))

    def test_wrong_legal_date_fails_closed(self):
        with self.assertRaises(RTModelContextError):
            validate_verified_live_record(
                live_record(),
                expected_as_of=date(2026, 8, 20),
            )

    def test_non_exact_canonical_url_fails_closed(self):
        record = live_record()
        record["canonical_url"] += "?foo=1"
        with self.assertRaises(RTModelContextError):
            validate_verified_live_record(record, expected_as_of=date(2026, 8, 21))

    def test_unverified_live_status_fails_closed(self):
        record = live_record()
        record["verification_status"] = "CURRENT_REVISION_VERIFIED"
        with self.assertRaises(RTModelContextError):
            admit_model_context([record], expected_as_of=date(2026, 8, 21))

    def test_local_fallback_requires_original_audited_candidate(self):
        fallback = local_fallback()
        with self.assertRaises(RTModelContextError):
            admit_model_context([fallback], expected_as_of=date(2026, 8, 21))

    def test_local_fallback_is_admitted_without_live_relabeling(self):
        candidate = local_candidate()
        fallback = local_fallback(candidate)
        admitted = admit_model_context(
            [fallback],
            expected_as_of=date(2026, 8, 21),
            local_reference={"TLS_95": candidate},
        )
        self.assertEqual(admitted["status"], "AUDITED_LOCAL_CONTEXT")
        self.assertEqual(admitted["live_count"], 0)
        self.assertEqual(
            admitted["laws"][0]["model_context_admission"],
            "AUDITED_LOCAL_CORPUS_FALLBACK",
        )
        self.assertEqual(
            admitted["laws"][0]["verification_status"],
            "LOCAL_CORPUS_FALLBACK",
        )

    def test_changed_local_fallback_fails_against_original_candidate(self):
        candidate = local_candidate()
        fallback = local_fallback(candidate)
        fallback["text"] += " võltsitud"
        fallback["content_hash"] = hashlib.sha256(
            fallback["text"].encode("utf-8")
        ).hexdigest()
        with self.assertRaises(RTModelContextError):
            admit_model_context(
                [fallback],
                local_reference={"TLS_95": candidate},
            )

    def test_unknown_untagged_record_is_not_admitted(self):
        with self.assertRaises(RTModelContextError):
            admit_model_context([local_candidate()])

    def test_mixed_context_retains_distinct_trust_labels(self):
        candidate = local_candidate()
        fallback = local_fallback(candidate)
        admitted = admit_model_context(
            [live_record(section="96"), fallback],
            expected_as_of=date(2026, 8, 21),
            local_reference={"TLS_95": candidate},
        )
        self.assertEqual(admitted["status"], "MIXED_VERIFIED_AND_LOCAL_CONTEXT")
        self.assertEqual(admitted["live_count"], 1)
        self.assertEqual(admitted["local_count"], 1)


if __name__ == "__main__":
    unittest.main()
