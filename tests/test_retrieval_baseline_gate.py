import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.verify_retrieval_baseline import BaselineError, verify_provenance


class RetrievalBaselineGateTests(unittest.TestCase):
    def _repo(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        return temp, root

    @staticmethod
    def _blob(root: Path, path: str) -> str:
        result = subprocess.run(
            ["git", "hash-object", f"--path={path}", "--", path],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def test_provenance_accepts_exact_worktree_content(self):
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        target = root / "services" / "legal_search.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('stable')\n", encoding="utf-8")
        rows = [{"path": "services/legal_search.py", "git_blob_sha": self._blob(root, "services/legal_search.py")}]
        self.assertEqual(verify_provenance(root, rows), 1)

    def test_provenance_rejects_retrieval_code_drift(self):
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        target = root / "services" / "legal_search.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('baseline')\n", encoding="utf-8")
        expected = self._blob(root, "services/legal_search.py")
        target.write_text("print('changed')\n", encoding="utf-8")
        with self.assertRaises(BaselineError) as ctx:
            verify_provenance(
                root,
                [{"path": "services/legal_search.py", "git_blob_sha": expected}],
            )
        self.assertIn("baseline is stale", str(ctx.exception))

    def test_provenance_rejects_duplicate_paths(self):
        temp, root = self._repo()
        self.addCleanup(temp.cleanup)
        target = root / "eval" / "query_cases.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps([]), encoding="utf-8")
        sha = self._blob(root, "eval/query_cases.json")
        with self.assertRaises(BaselineError):
            verify_provenance(
                root,
                [
                    {"path": "eval/query_cases.json", "git_blob_sha": sha},
                    {"path": "eval/query_cases.json", "git_blob_sha": sha},
                ],
            )


if __name__ == "__main__":
    unittest.main()
