import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from common.training_snapshot import load_snapshot, prepare_snapshot, require_champion_absent
from evaluation import comparison, evaluate
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE, PERMISSION_DENIED, RESOURCE_DOES_NOT_EXIST
from random_forest import train

import mlflow


class BootstrapTests(unittest.TestCase):
    def test_existing_champion_blocks_before_snapshot_files_are_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = MagicMock()
            client.get_registered_model.return_value = SimpleNamespace(aliases={"champion": "1"})
            with self.assertRaisesRegex(ValueError, "absent champion"):
                prepare_snapshot(client, "model", root / "not-read.csv", root, "a" * 40, bootstrap=True)
            self.assertEqual(list(root.iterdir()), [])
            client.get_model_version_by_alias.assert_not_called()

    def test_bootstrap_snapshot_rejects_champion_model_and_mode_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = MagicMock()
            client.get_registered_model.return_value = SimpleNamespace(aliases={})
            data = root / "source.csv"
            data.write_bytes(b"feature,target\n1,2\n")
            output = root / "snapshot"
            output.mkdir()
            snapshot = prepare_snapshot(client, "model", data, output, "a" * 40, bootstrap=True)
            manifest = output / "snapshot.json"
            manifest.chmod(0o600)
            try:
                for key, value in (
                    ("champion", {"version": "1"}),
                    ("registered_model", "other-model"),
                    ("mode", "invalid"),
                    ("mode", "relative"),
                ):
                    with self.subTest(key=key, value=value):
                        manifest.write_text(json.dumps({**snapshot, key: value}))
                        with self.assertRaises(ValueError):
                            load_snapshot(manifest, data.read_bytes(), "a" * 40, "model")
            finally:
                (output / "SeoulBikeData.csv").chmod(0o600)

    def test_only_confirmed_missing_model_or_alias_allows_bootstrap(self):
        client = MagicMock()
        client.get_registered_model.return_value = SimpleNamespace(aliases={})
        require_champion_absent(client, "model")
        client.get_registered_model.return_value.aliases = {"champion": "1"}
        with self.assertRaisesRegex(ValueError, "absent champion"):
            require_champion_absent(client, "model")
        client.get_registered_model.side_effect = MlflowException("Missing model", error_code=RESOURCE_DOES_NOT_EXIST)
        require_champion_absent(client, "model")
        for error in (
            MlflowException("Access denied", error_code=PERMISSION_DENIED),
            MlflowException("Bad request", error_code=INVALID_PARAMETER_VALUE),
            RuntimeError("Network unavailable"),
        ):
            with self.subTest(error=error):
                client.get_registered_model.side_effect = error
                with self.assertRaises(type(error)):
                    require_champion_absent(client, "model")
        client.get_model_version_by_alias.assert_not_called()

    def test_thresholds_are_inclusive_and_both_required(self):
        for r2, rmse, passed in (
            (0.80, 300.0, True),
            (0.9, 250.0, True),
            (0.7999999999, 250.0, False),
            (0.9, 300.0000000001, False),
            (0.7, 400.0, False),
        ):
            with self.subTest(r2=r2, rmse=rmse):
                metrics = {"r2": r2, "rmse": rmse, "mae": 1.0, "mape": 0.1}
                with patch.object(comparison, "evaluate_model", return_value=metrics) as measure:
                    report = comparison.evaluate_bootstrap({"model_uri": "models:/m-test"}, {}, None, None)
                self.assertEqual(report["passed"], passed)
                self.assertEqual(report["mode"], "bootstrap")
                self.assertIsNone(report["champion"])
                self.assertEqual(report["thresholds"], {"r2_min": 0.80, "rmse_max": 300.0})
                measure.assert_called_once()

    def test_bootstrap_does_not_hide_model_loading_or_invalid_metrics(self):
        for error in (RuntimeError("Cannot load model"), ValueError("Missing or non-finite evaluation metrics")):
            with self.subTest(error=error):
                with patch.object(comparison, "evaluate_model", side_effect=error):
                    with self.assertRaises(type(error)):
                        comparison.evaluate_bootstrap({"model_uri": "models:/m-test"}, {}, None, None)

    def test_real_bootstrap_training_evaluation_and_summary_without_registration(self):
        previous_tracking = mlflow.get_tracking_uri()
        self.addCleanup(mlflow.set_tracking_uri, previous_tracking)
        with tempfile.TemporaryDirectory(prefix="mlops-bootstrap-") as directory:
            root = Path(directory)
            tracking = f"sqlite:///{root / 'mlflow.db'}"
            environment = {
                "MLFLOW_TRACKING_URI": tracking,
                "MLFLOW_EXPERIMENT_NAME": "bootstrap-test",
                "GIT_COMMIT": "a" * 40,
            }
            with patch.dict(os.environ, environment):
                mlflow.set_tracking_uri(tracking)
                mlflow.create_experiment("bootstrap-test", artifact_location=str(root / "artifacts"))
                client = mlflow.MlflowClient()
                data = root / "input.csv"
                values = np.arange(100, dtype=float)
                pd.DataFrame(
                    {
                        "Date": ["date"] * 100,
                        "Seasons": ["Summer", "Winter"] * 50,
                        "Holiday": ["No Holiday"] * 100,
                        "Functioning Day": ["Yes"] * 100,
                        "Temperature": values,
                        "Rented Bike Count": values * 2 + 100,
                    }
                ).to_csv(data, index=False)
                snapshot_directory = root / "snapshot"
                snapshot_directory.mkdir()
                # Ordinary runs never fall back to bootstrap for a missing champion.
                with self.assertRaises(MlflowException):
                    prepare_snapshot(client, "new-model", data, snapshot_directory, "a" * 40)
                self.assertEqual(list(snapshot_directory.iterdir()), [])
                snapshot = prepare_snapshot(client, "new-model", data, snapshot_directory, "a" * 40, bootstrap=True)
                self.assertIsNone(snapshot["champion"])
                self.assertEqual(snapshot["mode"], "bootstrap")
                snapshot_path = snapshot_directory / "snapshot.json"
                csv_path = snapshot_directory / "SeoulBikeData.csv"
                self.assertEqual(load_snapshot(snapshot_path, csv_path.read_bytes(), "a" * 40, "new-model"), snapshot)
                for estimators, depth, passed in ((10, 5, True), (1, 1, False)):
                    with self.subTest(passed=passed):
                        test_set = root / f"test-set-{estimators}"
                        test_set.mkdir()
                        training_result = root / f"training-{estimators}.json"
                        args = argparse.Namespace(
                            training_data=str(csv_path),
                            n_estimators=estimators,
                            max_depth=depth,
                            registered_model_name="new-model",
                            skip_registration=True,
                            result_json=training_result,
                            input_snapshot=snapshot_path,
                            test_set_directory=test_set,
                            fail_for_alert_test=False,
                        )
                        train.main(args)
                        # The CLI must opt in explicitly, even with a valid bootstrap snapshot.
                        with self.assertRaisesRegex(ValueError, "explicit evaluation mode"):
                            evaluate.evaluate_training(
                                training_result, snapshot_path, test_set, root / "wrong.json", "a" * 40
                            )
                        report_path = root / f"report-{estimators}.json"
                        process = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "evaluation.evaluate",
                                "--bootstrap",
                                "--training-result",
                                str(training_result),
                                "--input-snapshot",
                                str(snapshot_path),
                                "--test-set-directory",
                                str(test_set),
                                "--report",
                                str(report_path),
                                "--expected-commit",
                                "a" * 40,
                            ],
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(process.returncode, 0 if passed else 1, process.stdout + process.stderr)
                        if not passed:
                            self.assertIn("Evaluation rejected: bootstrap thresholds not met", process.stderr)
                        report = json.loads(report_path.read_text())
                        self.assertEqual(report["passed"], passed)
                        self.assertEqual(report["mode"], "bootstrap")
                        self.assertNotIn("champion_metrics", report)
                        result = json.loads(training_result.read_text())
                        run = client.get_run(result["run_id"])
                        self.assertEqual(run.info.status, "FINISHED")
                        self.assertEqual(run.data.tags["evaluation.passed"], str(passed).lower())
                        self.assertEqual(client.search_registered_models(), [])
                        saved_report = Path(client.download_artifacts(result["run_id"], "evaluation/report.json"))
                        self.assertEqual(json.loads(saved_report.read_text()), report)
                        self.assertEqual(
                            {item.path for item in client.list_artifacts(result["run_id"], "evaluation")},
                            {
                                "evaluation/input-snapshot.json",
                                "evaluation/test-set-manifest.json",
                                "evaluation/report.json",
                            },
                        )
                        summary = root / f"summary-{estimators}.md"
                        reporter = (
                            Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "report-training-evaluation.py"
                        )
                        subprocess.run(
                            [
                                sys.executable,
                                str(reporter),
                                "--result",
                                str(training_result),
                                "--evaluation",
                                str(report_path),
                                "--summary",
                                str(summary),
                            ],
                            check=True,
                            capture_output=True,
                        )
                        text = summary.read_text()
                        self.assertIn("Bootstrap evaluation", text)
                        self.assertIn("Required R2 >= `0.8`", text)
                        self.assertIn("Required RMSE <= `300.0`", text)
                        self.assertIn(f"Passed: `{passed}`", text)
                # A registered model without the alias also permits bootstrap.
                client.create_registered_model("new-model")
                require_champion_absent(client, "new-model")


if __name__ == "__main__":
    unittest.main()
