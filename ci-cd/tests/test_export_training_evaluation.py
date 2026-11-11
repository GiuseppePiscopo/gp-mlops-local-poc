import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export-training-evaluation.py"


class ExportTrainingEvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.result_path = self.root / "result.json"
        self.report_path = self.root / "report.json"
        self.output = self.root / "github-output"
        self.artifacts = self.root / "artifacts"
        self.registration = None
        self.candidate = {"run_id": "b" * 32, "model_uri": "models:/m-" + "c" * 32, "git_commit": "a" * 40}
        self.identity = {"dataset_sha256": "dataset", "test_values_sha256": "test-values", "test_rows": 20}
        self.result = {
            **self.candidate,
            "test_set_manifest": {"candidate": self.candidate, "test_set": self.identity},
        }
        self.report = {
            "mode": "relative",
            "candidate": self.candidate,
            "champion": {"registered_model": "model", "version": "1", "run_id": "d" * 32},
            "test_set": self.identity,
            "candidate_metrics": {"rmse": 10.0, "mae": 8.0, "r2": 0.9, "mape": 0.2},
            "champion_metrics": {"rmse": 10.0, "mae": 8.0, "r2": 0.9, "mape": 0.2},
            "passed": True,
        }

    def export(self, run_id="123", attempt="2", *, write_report=True):
        self.result_path.write_text(json.dumps(self.result))
        if write_report:
            self.report_path.write_text(json.dumps(self.report))
        registration_args = []
        if self.registration is not None:
            path = self.root / "registration.json"
            path.write_text(json.dumps(self.registration))
            registration_args = ["--registration", str(path)]
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--result",
                str(self.result_path),
                "--evaluation",
                str(self.report_path),
                "--expected-commit",
                "a" * 40,
                "--workflow-run-id",
                run_id,
                "--workflow-run-attempt",
                attempt,
                "--artifact-directory",
                str(self.artifacts),
                "--github-output",
                str(self.output),
                "--summary",
                str(self.root / "summary.md"),
                *registration_args,
            ],
            capture_output=True,
            text=True,
        )

    def test_exact_metadata_bundle_and_outputs_for_both_gate_outcomes(self):
        self.output.write_text("existing=value\n")
        # An unrelated file and extra training fields must not enter the artifact.
        (self.root / "test-data.json").write_text("ephemeral data")
        self.result["unrelated"] = "not exported"
        for passed in (True, False):
            with self.subTest(passed=passed):
                self.artifacts = self.root / f"artifacts-{passed}"
                self.report["passed"] = passed
                process = self.export()
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(
                    {item.name for item in self.artifacts.iterdir()},
                    {"training-result.json", "evaluation-report.json", "execution.json"},
                )
                training = json.loads((self.artifacts / "training-result.json").read_text())
                self.assertEqual(training, {key: value for key, value in self.result.items() if key != "unrelated"})
                self.assertEqual(json.loads((self.artifacts / "evaluation-report.json").read_text()), self.report)
                execution = json.loads((self.artifacts / "execution.json").read_text())
                self.assertEqual(execution["workflow_run_id"], "123")
                self.assertEqual(execution["workflow_run_attempt"], "2")
                self.assertEqual(execution["run_id"], self.candidate["run_id"])
                self.assertEqual(execution["artifact_name"], "training-evaluation-123-2")
                self.assertIs(execution["evaluation_passed"], passed)
                text = self.output.read_text()
                self.assertTrue(text.startswith("existing=value\n"))
                for key, value in self.candidate.items():
                    self.assertIn(f"{key}={value}\n", text)
                self.assertIn(f"evaluation_passed={str(passed).lower()}\n", text)
                self.assertNotIn("model_version=", text)

    def test_bootstrap_bundle_has_no_invented_champion_metrics(self):
        self.report.update(mode="bootstrap", champion=None, thresholds={"r2_min": 0.8, "rmse_max": 300.0})
        del self.report["champion_metrics"]
        process = self.export()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads((self.artifacts / "evaluation-report.json").read_text()), self.report)
        self.assertIn("evaluation_mode=bootstrap\n", self.output.read_text())

    def test_incoherent_inputs_never_publish_outputs_or_bundle(self):
        original_result, original_report = copy.deepcopy(self.result), copy.deepcopy(self.report)
        for scenario in ("commit", "candidate", "manifest", "test_set", "passed", "mode", "metrics", "newline"):
            with self.subTest(scenario=scenario):
                self.result, self.report = copy.deepcopy(original_result), copy.deepcopy(original_report)
                if scenario == "commit":
                    self.result["git_commit"] = "f" * 40
                elif scenario == "candidate":
                    self.report["candidate"]["run_id"] = "e" * 32
                elif scenario == "manifest":
                    self.result["test_set_manifest"]["candidate"]["run_id"] = "e" * 32
                elif scenario == "test_set":
                    self.report["test_set"]["test_rows"] = 10
                elif scenario == "passed":
                    self.report["passed"] = "false"
                elif scenario == "mode":
                    self.report["mode"] = "unknown"
                elif scenario == "metrics":
                    self.report["candidate_metrics"]["rmse"] = float("nan")
                else:
                    self.result["model_uri"] += "\ninjected=value"
                process = self.export()
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("export failed", process.stderr)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.artifacts.exists())

    def test_invalid_execution_identity_cannot_inject_outputs(self):
        for run_id, attempt in (("0", "1"), ("123", "0"), ("123\ninjected=value", "1"), ("123", "../2")):
            with self.subTest(run_id=run_id, attempt=attempt):
                process = self.export(run_id, attempt)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.artifacts.exists())

    def test_existing_bundle_is_not_overwritten(self):
        self.artifacts.mkdir()
        previous = self.artifacts / "training-result.json"
        previous.write_text("existing")
        process = self.export()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(previous.read_text(), "existing")
        self.assertFalse(self.output.exists())

    def test_missing_report_cannot_publish_outputs(self):
        process = self.export(write_report=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("export failed", process.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.artifacts.exists())

    def test_registration_adds_exact_version_outputs_receipt_and_summary(self):
        self.registration = {
            **self.candidate,
            "registered_model": "model",
            "model_version": "3",
            "status": "READY",
        }
        process = self.export()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads((self.artifacts / "registration-result.json").read_text()), self.registration)
        execution = json.loads((self.artifacts / "execution.json").read_text())
        self.assertEqual(execution["registered_model"], "model")
        self.assertEqual(execution["model_version"], "3")
        self.assertIn("model_version=3\n", self.output.read_text())
        self.assertIn("registered_model=model\n", self.output.read_text())
        self.assertIn("Model version: `3`", (self.root / "summary.md").read_text())

    def test_invalid_registration_cannot_publish_a_version(self):
        receipt = {**self.candidate, "registered_model": "model", "model_version": "3", "status": "READY"}
        for key, value in (
            ("run_id", "e" * 32),
            ("git_commit", "f" * 40),
            ("model_uri", "models:/m-" + "d" * 32),
            ("status", "PENDING_REGISTRATION"),
            ("model_version", "0"),
            ("registered_model", "model\ninjected=value"),
        ):
            with self.subTest(key=key):
                self.registration = {**receipt, key: value}
                process = self.export()
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.artifacts.exists())
        self.registration = receipt
        self.report["passed"] = False
        process = self.export()
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
