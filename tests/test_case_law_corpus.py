import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from services.case_law_corpus import (
    CaseLawCorpusError,
    build_manifest,
    canonicalize_import_rows,
    serialize_corpus,
    verify_case_law_corpus,
    write_corpus_and_manifest,
)


class CaseLawCorpusTests(unittest.TestCase):
    def _row(self):
        return {
            "court_name": "Riigikohtu halduskolleegium",
            "case_number": "3-22-348/41",
            "decision_date": "2026-05-05",
            "decision_type": "otsus",
            "court_level": "supreme",
            "canonical_url": "https://www.riigiteataja.ee/kohtulahendid/fiktiivne-test-url",
            "text": (
                "See on testis kasutatav piisavalt pikk kohtulahendi alliktekst. "
                "Seda ei esitata päris kohtulahendina ning selle ainus eesmärk on "
                "kontrollida V11.1 korpuse deterministlikku provenance-piiri."
            ),
        }

    def test_empty_committed_shape_passes(self):
        records = []
        corpus_bytes = serialize_corpus(records)
        manifest = build_manifest(corpus_bytes, records)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "case_law.json"
            manifest_path = root / "case_law_manifest.json"
            corpus.write_bytes(corpus_bytes)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = verify_case_law_corpus(corpus_path=corpus, manifest_path=manifest_path)
        self.assertEqual(report["record_count"], 0)
        self.assertFalse(report["retrieval_enabled"])

    def test_import_canonicalizes_hashes_and_sorts(self):
        first = self._row()
        second = copy.deepcopy(first)
        second["case_number"] = "3-22-1043/67"
        second["decision_date"] = "2026-02-12"
        records = canonicalize_import_rows([first, second], today=date(2026, 8, 21))
        self.assertEqual([r["id"] for r in records], sorted(r["id"] for r in records))
        self.assertTrue(all(len(r["record_sha256"]) == 64 for r in records))
        self.assertNotIn("authority_status", records[0])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "case_law.json"
            manifest_path = root / "case_law_manifest.json"
            write_corpus_and_manifest(records, corpus_path=corpus, manifest_path=manifest_path)
            report = verify_case_law_corpus(corpus_path=corpus, manifest_path=manifest_path)
        self.assertEqual(report["record_count"], 2)

    def test_import_rejects_non_official_url(self):
        row = self._row()
        row["canonical_url"] = "https://example.com/judgment"
        with self.assertRaises(CaseLawCorpusError):
            canonicalize_import_rows([row], today=date(2026, 8, 21))

    def test_import_rejects_future_decision(self):
        row = self._row()
        row["decision_date"] = "2026-08-22"
        with self.assertRaises(CaseLawCorpusError):
            canonicalize_import_rows([row], today=date(2026, 8, 21))

    def test_crlf_worktree_line_endings_match_canonical_manifest_hash(self):
        records = canonicalize_import_rows([self._row()], today=date(2026, 8, 21))
        corpus_bytes = serialize_corpus(records)
        manifest = build_manifest(corpus_bytes, records)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "case_law.json"
            manifest_path = root / "case_law_manifest.json"
            corpus.write_bytes(corpus_bytes.replace(b"\n", b"\r\n"))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = verify_case_law_corpus(corpus_path=corpus, manifest_path=manifest_path)
        self.assertEqual(report["corpus_sha256"], manifest["corpus_sha256"])

    def test_corpus_byte_tamper_fails_closed(self):
        records = canonicalize_import_rows([self._row()], today=date(2026, 8, 21))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "case_law.json"
            manifest_path = root / "case_law_manifest.json"
            write_corpus_and_manifest(records, corpus_path=corpus, manifest_path=manifest_path)
            corpus.write_text(corpus.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(CaseLawCorpusError):
                verify_case_law_corpus(corpus_path=corpus, manifest_path=manifest_path)

    def test_manifest_cannot_enable_retrieval_or_model_context(self):
        records = []
        corpus_bytes = serialize_corpus(records)
        manifest = build_manifest(corpus_bytes, records)
        manifest["retrieval_enabled"] = True
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "case_law.json"
            manifest_path = root / "case_law_manifest.json"
            corpus.write_bytes(corpus_bytes)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(CaseLawCorpusError):
                verify_case_law_corpus(corpus_path=corpus, manifest_path=manifest_path)


if __name__ == "__main__":
    unittest.main()
