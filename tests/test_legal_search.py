import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from config import load_settings
from services.legal_search import (
    HistoricalDataUnavailableError,
    LegalDataUnavailableError,
    LegalSearchService,
)


class LegalSearchTests(unittest.TestCase):
    def _write_laws(self, directory: str, laws):
        path = Path(directory) / "laws.json"
        path.write_text(json.dumps(laws, ensure_ascii=False), encoding="utf-8")
        return path

    def _record(self, *, law_id="VOS_308", domain="VOS", title=None, text=None, aliases=None):
        text = text or "üürileping ülesütlemine tähtaeg"
        return {
            "id": law_id,
            "title": title or "Võlaõigusseadus § 308",
            "text": text,
            "source": f"Riigi Teataja: {domain}",
            "domain": domain,
            "law_name": "Testseadus",
            "section": law_id.split("_", 1)[1],
            "aliases": aliases or ["üür"],
            "url": f"https://www.riigiteataja.ee/akt/{domain}?leiaKehtiv#para308",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def test_empty_corpus_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_laws(tmp, [])
            with self.assertRaises(LegalDataUnavailableError):
                LegalSearchService(use_riigi_teataja=False, data_file=path)

    def test_old_mock_shape_is_rejected_as_untrusted_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_laws(
                tmp,
                [{
                    "id": "VOS_308",
                    "title": "Võlaõigusseadus § 308",
                    "text": "mock",
                    "source": "Riigi Teataja: VÕS",
                }],
            )
            with self.assertRaises(LegalDataUnavailableError):
                LegalSearchService(use_riigi_teataja=False, data_file=path)

    def test_tampered_content_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._record()
            record["content_hash"] = "0" * 64
            path = self._write_laws(tmp, [record])
            with self.assertRaises(LegalDataUnavailableError):
                LegalSearchService(use_riigi_teataja=False, data_file=path)

    def test_ids_are_canonicalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._record(
                law_id="AvTS_10",
                domain="AvTS",
                title="Avaliku teabe seadus § 10",
                text="avalik teave teabevaldaja",
                aliases=["avalik teave"],
            )
            path = self._write_laws(tmp, [record])
            service = LegalSearchService(use_riigi_teataja=False, data_file=path)
            self.assertEqual(service.laws[0]["id"], "AVTS_10")
            self.assertEqual(service.laws[0]["domain"], "AVTS")

    def test_past_date_without_temporal_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_laws(tmp, [self._record()])
            service = LegalSearchService(use_riigi_teataja=False, data_file=path)
            past = (date.today() - timedelta(days=30)).isoformat()
            with self.assertRaises(HistoricalDataUnavailableError):
                service.search_laws("üürilepingu ülesütlemine", past)

    def test_future_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_laws(tmp, [self._record()])
            service = LegalSearchService(use_riigi_teataja=False, data_file=path)
            future = (date.today() + timedelta(days=30)).isoformat()
            with self.assertRaises(HistoricalDataUnavailableError):
                service.search_laws("üürilepingu ülesütlemine", future)

    def test_retrieval_limits_come_from_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [
                self._record(
                    law_id=f"VOS_{number}",
                    text=f"üürileping ülesütlemine tähtaeg {number}",
                    aliases=["üür", "ülesütlemine"],
                )
                for number in (301, 302, 303)
            ]
            path = self._write_laws(tmp, records)
            settings = load_settings({
                "LEGAL_MIN_SCORE": "1",
                "LEGAL_MAX_RESULTS": "2",
                "LEGAL_RELATIVE_THRESHOLD": "0",
            })
            service = LegalSearchService(
                use_riigi_teataja=False,
                data_file=path,
                settings=settings,
            )
            results = service.search_laws("üürileping ülesütlemine", "")
            self.assertEqual(len(results), 2)

    def test_auxiliary_police_fine_uses_audited_sections_not_adjacent_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [
                self._record(
                    law_id="ABIPOLS_3", domain="ABIPOLS",
                    title="Abipolitseiniku seadus § 3",
                    text="Abipolitseiniku pädevuses on politsei abistamine.",
                    aliases=["abipolitseinik", "pädevus"],
                ),
                self._record(
                    law_id="ABIPOLS_16", domain="ABIPOLS",
                    title="Abipolitseiniku seadus § 16",
                    text="Abipolitseinik võib rakendada loetletud riikliku järelevalve meetmeid.",
                    aliases=["abipolitseinik", "riiklik järelevalve"],
                ),
                self._record(
                    law_id="VTMS_19", domain="VTMS",
                    title="Väärteomenetluse seadustik § 19",
                    text="Menetlusalusel isikul on väärteomenetluses õigus teada asja ja esitada tõendeid.",
                    aliases=["menetlusaluse isiku õigused"],
                ),
                self._record(
                    law_id="VTMS_57", domain="VTMS",
                    title="Väärteomenetluse seadustik § 57",
                    text="Kiirmenetluse otsuses märgitakse rahatrahv, rikkumine ja tõendid.",
                    aliases=["kiirmenetluse otsus", "trahv"],
                ),
                self._record(
                    law_id="VTMS_114", domain="VTMS",
                    title="Väärteomenetluse seadustik § 114",
                    text="Menetlusosalisel on õigus esitada otsuse peale maakohtule kaebus.",
                    aliases=["kaebus", "vaidlustamine"],
                ),
                self._record(
                    law_id="ABIPOLS_40", domain="ABIPOLS",
                    title="Abipolitseiniku seadus § 40",
                    text="Abipolitseiniku kulutuste hüvitamine.",
                    aliases=["abipolitseinik"],
                ),
                self._record(
                    law_id="ABIPOLS_42", domain="ABIPOLS",
                    title="Abipolitseiniku seadus § 42",
                    text="Abipolitseiniku staatusest vabastamine.",
                    aliases=["abipolitseinik"],
                ),
            ]
            path = self._write_laws(tmp, records)
            settings = load_settings({
                "LEGAL_MIN_SCORE": "1",
                "LEGAL_MAX_RESULTS": "5",
                "LEGAL_RELATIVE_THRESHOLD": "0",
            })
            service = LegalSearchService(data_file=path, settings=settings)

            laws, interpretation = service.search_laws_with_context(
                "Abipolitsei trahvis mind ilma asjata", ""
            )

            expected = {"ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114"}
            self.assertEqual({law["id"] for law in laws}, expected)
            self.assertEqual(set(interpretation.section_hints), expected)
            self.assertNotIn("ABIPOLS_42", {law["id"] for law in laws})


if __name__ == "__main__":
    unittest.main()
