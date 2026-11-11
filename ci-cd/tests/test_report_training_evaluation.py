import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "report-training-evaluation.py"


class ReportTrainingEvaluationTests(unittest.TestCase):
    def test_incoherent_or_missing_report_does_not_append_summary(self):
        candidate = {"run_id": "candidate-run", "model_uri": "models:/m-candidate", "git_commit": "a" * 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "training.json"
            report = root / "report.json"
            summary = root / "summary.md"
            result.write_text(json.dumps(candidate))
            summary.write_text("Existing summary\n")
            for key, value in (
                ("run_id", "another-run"),
                ("model_uri", "models:/m-other"),
                ("git_commit", "b" * 40),
                (None, None),
            ):
                with self.subTest(key=key):
                    if key is None:
                        report.unlink()
                    else:
                        report.write_text(json.dumps({"candidate": {**candidate, key: value}}))
                    process = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--result",
                            str(result),
                            "--evaluation",
                            str(report),
                            "--summary",
                            str(summary),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    if key is not None:
                        self.assertIn("does not match the training result", process.stderr)
                    self.assertEqual(summary.read_text(), "Existing summary\n")


if __name__ == "__main__":
    unittest.main()
