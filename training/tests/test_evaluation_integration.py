import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from common.test_set_persistence import load_test_set
from common.training_snapshot import DATASET_FILENAME, SNAPSHOT_FILENAME, prepare_snapshot
from evaluation import evaluate
from random_forest import train

import mlflow


class EvaluationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mlops-gate-integration-")))
        tracking = f"sqlite:///{self.root / 'mlflow.db'}"
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "MLFLOW_TRACKING_URI": tracking,
                    "MLFLOW_EXPERIMENT_NAME": "isolated-gate",
                    "GIT_COMMIT": "a" * 40,
                },
            )
        )
        self.addCleanup(mlflow.set_tracking_uri, mlflow.get_tracking_uri())
        mlflow.set_tracking_uri(tracking)
        mlflow.create_experiment("isolated-gate", artifact_location=str(self.root / "artifacts"))
        self.dataset = self.root / "data.csv"
        values = np.arange(100, dtype=float)
        pd.DataFrame(
            {
                "Date": ["date"] * 100,
                "Seasons": ["Summer", "Winter"] * 50,
                "Holiday": ["No Holiday"] * 100,
                "Functioning Day": ["Yes"] * 100,
                "Temperature": values,
                "Rented Bike Count": values**2 + 1,
            }
        ).to_csv(self.dataset, index=False)
        self.original_bytes = self.dataset.read_bytes()
        self.args = argparse.Namespace(
            training_data=str(self.dataset),
            n_estimators=10,
            max_depth=5,
            registered_model_name="isolated-model",
            skip_registration=False,
            result_json=self.root / "baseline.json",
            input_snapshot=None,
            test_set_directory=None,
            fail_for_alert_test=False,
        )
        with patch.object(train, "RandomForestRegressor", wraps=train.RandomForestRegressor) as forest:
            train.main(self.args)
        self.assertEqual(forest.call_args.kwargs["n_jobs"], -1)
        self.client = mlflow.MlflowClient()
        self.client.set_registered_model_alias("isolated-model", "champion", "1")
        snapshot_directory = self.root / "snapshot"
        snapshot_directory.mkdir()
        prepare_snapshot(self.client, "isolated-model", self.dataset, snapshot_directory, "a" * 40)
        self.args.training_data = str(snapshot_directory / DATASET_FILENAME)
        self.args.input_snapshot = snapshot_directory / SNAPSHOT_FILENAME
        self.args.test_set_directory = self.root / "test-set"
        self.args.test_set_directory.mkdir()
        self.args.skip_registration = True
        self.args.result_json = self.root / "candidate.json"

    def run_evaluation_cli(self, report_path):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "evaluation.evaluate",
                "--training-result",
                str(self.args.result_json),
                "--input-snapshot",
                str(self.args.input_snapshot),
                "--test-set-directory",
                str(self.args.test_set_directory),
                "--report",
                str(report_path),
                "--expected-commit",
                "a" * 40,
            ],
            capture_output=True,
            text=True,
        )

    def assert_exported_results(self, report_path, passed):
        exporter = Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "export-training-evaluation.py"
        bundle = self.root / "exported"
        outputs = self.root / "github-output"
        subprocess.run(
            [
                sys.executable,
                str(exporter),
                "--result",
                str(self.args.result_json),
                "--evaluation",
                str(report_path),
                "--expected-commit",
                "a" * 40,
                "--workflow-run-id",
                "123",
                "--workflow-run-attempt",
                "1",
                "--artifact-directory",
                str(bundle),
                "--github-output",
                str(outputs),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads((bundle / "evaluation-report.json").read_text()), json.loads(report_path.read_text())
        )
        self.assertIs(json.loads((bundle / "execution.json").read_text())["evaluation_passed"], passed)
        self.assertIn(f"evaluation_passed={str(passed).lower()}\n", outputs.read_text())

    def assert_registry_unchanged(self):
        self.assertEqual(len(self.client.search_model_versions("name='isolated-model'")), 1)
        self.assertEqual(str(self.client.get_model_version_by_alias("isolated-model", "champion").version), "1")

    def test_equal_candidate_uses_fixed_snapshot_without_csv_or_preprocessing(self):
        train_model = train.train_model

        def mutate_after_training(*args, **kwargs):
            model = train_model(*args, **kwargs)
            self.dataset.write_text("changed CSV after training")
            return model

        with patch.object(train, "train_model", side_effect=mutate_after_training):
            train.main(self.args)
        result = json.loads(self.args.result_json.read_text())
        self.assertNotIn("evaluation", result)
        self.assertEqual(self.client.get_run(result["run_id"]).info.status, "FINISHED")
        self.dataset.unlink()
        Path(self.args.training_data).unlink()
        report_path = self.root / "report.json"
        with (
            patch.object(train, "prepare_data", side_effect=AssertionError("Preprocessing repeated")),
            patch.object(self.client, "get_model_version_by_alias", side_effect=AssertionError("Alias reread")),
            patch.object(evaluate.mlflow, "MlflowClient", return_value=self.client),
        ):
            report = evaluate.evaluate_training(
                self.args.result_json, self.args.input_snapshot, self.args.test_set_directory, report_path, "a" * 40
            )
        self.assertEqual(report["test_set"], result["test_set_manifest"]["test_set"])
        self.assertTrue(report["passed"])
        self.assertEqual(report["candidate_metrics"], report["champion_metrics"])
        self.assertEqual(report["test_set"]["dataset_sha256"], hashlib.sha256(self.original_bytes).hexdigest())
        self.assertEqual(len(report["test_set"]["test_row_indices"]), 20)
        self.assertEqual(self.client.get_run(result["run_id"]).info.status, "FINISHED")
        self.assertEqual(
            {item.path for item in self.client.list_artifacts(result["run_id"], "evaluation")},
            {
                "evaluation/champion-snapshot.json",
                "evaluation/report.json",
                "evaluation/input-snapshot.json",
                "evaluation/test-set-manifest.json",
            },
        )
        candidate = {key: result[key] for key in ("run_id", "model_uri", "git_commit")}
        snapshot = json.loads(self.args.input_snapshot.read_text())
        with patch.object(train, "prepare_data", side_effect=AssertionError("Preprocessing repeated")):
            features, target = load_test_set(
                self.args.test_set_directory, result["test_set_manifest"], candidate, snapshot
            )
        self.assertEqual(len(features), 20)
        self.assertEqual(features.index.tolist(), report["test_set"]["test_row_indices"])
        self.assertEqual(len(target), 20)
        cli_report = self.root / "cli-report.json"
        process = self.run_evaluation_cli(cli_report)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(json.loads(cli_report.read_text()), report)
        self.assert_exported_results(cli_report, passed=True)
        self.assert_registry_unchanged()

    def test_regression_rejects_cli_but_preserves_report_summary_and_outputs(self):
        self.args.n_estimators = 1
        self.args.max_depth = 1
        train.main(self.args)
        result = json.loads(self.args.result_json.read_text())
        self.assertNotIn("evaluation", result)
        self.assertEqual(self.client.get_run(result["run_id"]).info.status, "FINISHED")
        report_path = self.root / "report.json"
        process = self.run_evaluation_cli(report_path)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertIn("Evaluation rejected", process.stderr)
        report = json.loads(report_path.read_text())
        self.assertFalse(report["passed"])
        self.assertGreater(report["candidate_metrics"]["rmse"], report["champion_metrics"]["rmse"])
        saved_report = Path(self.client.download_artifacts(result["run_id"], "evaluation/report.json"))
        self.assertEqual(json.loads(saved_report.read_text()), report)
        run = self.client.get_run(result["run_id"])
        self.assertEqual(run.data.tags["evaluation.passed"], "false")
        self.assertEqual(run.info.status, "FINISHED")
        summary = self.root / "summary.md"
        reporter = Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "report-training-evaluation.py"
        subprocess.run(
            [
                sys.executable,
                str(reporter),
                "--result",
                str(self.args.result_json),
                "--evaluation",
                str(report_path),
                "--summary",
                str(summary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        text = summary.read_text()
        self.assertIn("Passed: `False`", text)
        self.assertIn(f"| rmse | {report['candidate_metrics']['rmse']} | {report['champion_metrics']['rmse']} |", text)
        self.assert_exported_results(report_path, passed=False)
        self.assert_registry_unchanged()


if __name__ == "__main__":
    unittest.main()
