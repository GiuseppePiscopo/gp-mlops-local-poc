import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-image-provenance.py"


class VerifyImageProvenanceTests(unittest.TestCase):
    def test_cli(self):
        commit = "a" * 40
        valid = {
            "git_commit": commit,
            "image": f"gp-mof-capstone-training:git-{commit}",
            "image_id": "sha256:" + "b" * 64,
            "reused": True,
        }
        cases = {
            "valid": valid,
            "wrong_commit": {**valid, "git_commit": "c" * 40},
            "wrong_image": {**valid, "image": "gp-mof-capstone-training:dev"},
            "missing_fields": {"git_commit": commit},
            "invalid_json": None,
        }
        for name, result in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "build.json"
                summary = root / "summary"
                source.write_text(json.dumps(result) if result is not None else "{", encoding="utf-8")
                summary.write_text("Existing\n", encoding="utf-8")
                process = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--build-result",
                        str(source),
                        "--expected-commit",
                        commit,
                        "--summary",
                        str(summary),
                    ],
                    capture_output=True,
                    text=True,
                )
                if name == "valid":
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(json.loads(process.stdout), valid)
                    self.assertIn(f"- Commit: `{commit}`", summary.read_text())
                    self.assertTrue(summary.read_text().startswith("Existing\n"))
                else:
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn("verification failed", process.stderr)
                    self.assertEqual(summary.read_text(), "Existing\n")


if __name__ == "__main__":
    unittest.main()
