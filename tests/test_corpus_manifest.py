import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from scripts.verify_corpus_manifest import CorpusManifestError, verify_manifest
class CorpusManifestTests(unittest.TestCase):
    def _project(self, *, with_legacy=False):
        temp=tempfile.TemporaryDirectory(); root=Path(temp.name); (root/"data").mkdir(); (root/"eval").mkdir()
        (root/"data/laws.json").write_text(json.dumps([{"id":"TLS_1","title":"TLS § 1","text":"Test","source":"Riigi Teataja"}]),encoding="utf-8")
        if with_legacy: (root/"data/laws.pre_v5_1.json").write_text("[]",encoding="utf-8")
        (root/"data/corpus_manifest.json").write_text(json.dumps({"version":"V10.5-corpus-manifest-1","corpus_path":"data/laws.json","record_count":1,"git_blob_sha":"a"*40}),encoding="utf-8")
        (root/"eval/V61_CI_BASELINE.json").write_text(json.dumps({"audited_result":{"retrieval_passed":184},"provenance":[{"path":"data/laws.json","git_blob_sha":"a"*40}]}),encoding="utf-8")
        return temp,root
    @patch("scripts.verify_corpus_manifest._git_blob_sha",return_value="a"*40)
    def test_exact_manifest_passes(self,_hash):
        temp,root=self._project(); self.addCleanup(temp.cleanup); report=verify_manifest(root); self.assertEqual(report["record_count"],1); self.assertTrue(report["legacy_snapshot_absent"])
    @patch("scripts.verify_corpus_manifest._git_blob_sha",return_value="b"*40)
    def test_corpus_blob_drift_fails_closed(self,_hash):
        temp,root=self._project(); self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(CorpusManifestError,"Corpus content drift"): verify_manifest(root)
    @patch("scripts.verify_corpus_manifest._git_blob_sha",return_value="a"*40)
    def test_legacy_snapshot_is_rejected(self,_hash):
        temp,root=self._project(with_legacy=True); self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(CorpusManifestError,"Legacy corpus snapshot"): verify_manifest(root)
if __name__=="__main__": unittest.main()
