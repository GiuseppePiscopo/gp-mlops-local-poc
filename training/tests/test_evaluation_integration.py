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
    def test_real_models_snapshot_and_rejected_gate_without_registration(self):
        previous_tracking = mlflow.get_tracking_uri()
        self.addCleanup(mlflow.set_tracking_uri, previous_tracking)
        with tempfile.TemporaryDirectory(prefix="mlops-gate-integration-") as directory:
            root = Path(directory)
            tracking = f"sqlite:///{root / 'mlflow.db'}"
            environment = {
                "MLFLOW_TRACKING_URI": tracking,
                "MLFLOW_EXPERIMENT_NAME": "isolated-gate",
                "GIT_COMMIT": "a" * 40,
            }
            with patch.dict(os.environ, environment):
                mlflow.set_tracking_uri(tracking)
                mlflow.create_experiment("isolated-gate", artifact_location=str(root / "artifacts"))
                path = root / "data.csv"
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
                ).to_csv(path, index=False)
                original_bytes = path.read_bytes()
                args = argparse.Namespace(
                    training_data=str(path),
                    n_estimators=10,
                    max_depth=5,
                    registered_model_name="isolated-model",
                    skip_registration=False,
                    result_json=root / "baseline.json",
                    input_snapshot=None,
                    test_set_directory=None,
                    fail_for_alert_test=False,
                )

                with patch.object(train, "RandomForestRegressor", wraps=train.RandomForestRegressor) as forest:
                    train.main(args)
                    self.assertEqual(forest.call_args.kwargs["n_jobs"], -1)
                    client = mlflow.MlflowClient()
                    client.set_registered_model_alias("isolated-model", "champion", "1")
                    snapshot_directory = root / "snapshot"
                    snapshot_directory.mkdir()
                    prepare_snapshot(client, "isolated-model", path, snapshot_directory, "a" * 40)
                    args.training_data = str(snapshot_directory / DATASET_FILENAME)
                    args.input_snapshot = snapshot_directory / SNAPSHOT_FILENAME
                    args.test_set_directory = root / "test-set"
                    args.test_set_directory.mkdir()
                    args.skip_registration = True
                    args.result_json = root / "candidate.json"
                    train_model = train.train_model

                    def mutate_after_training(*a, **kwargs):
                        model = train_model(*a, **kwargs)
                        path.write_text("changed CSV after training")
                        return model

                    with (
                        patch.object(train, "train_model", side_effect=mutate_after_training),
                    ):
                        train.main(args)
                    result = json.loads(args.result_json.read_text())
                    self.assertNotIn("evaluation", result)
                    self.assertEqual(client.get_run(result["run_id"]).info.status, "FINISHED")
                    path.unlink()
                    snapshot_csv = Path(args.training_data)
                    snapshot_csv.unlink()
                    evaluation_output = root / "report.json"
                    # The gate must work after training ends, without the CSV or another split.
                    with (
                        patch.object(train, "prepare_data", side_effect=AssertionError("Preprocessing repeated")),
                        patch.object(client, "get_model_version_by_alias", side_effect=AssertionError("Alias reread")),
                        patch.object(evaluate.mlflow, "MlflowClient", return_value=client),
                    ):
                        report = evaluate.evaluate_training(
                            args.result_json, args.input_snapshot, args.test_set_directory, evaluation_output, "a" * 40
                        )
                    self.assertEqual(report["test_set"], result["test_set_manifest"]["test_set"])
                    self.assertTrue(report["passed"])
                    self.assertEqual(report["candidate_metrics"], report["champion_metrics"])
                    self.assertEqual(report["test_set"]["dataset_sha256"], hashlib.sha256(original_bytes).hexdigest())
                    self.assertEqual(len(report["test_set"]["test_row_indices"]), 20)
                    self.assertEqual(client.get_run(result["run_id"]).info.status, "FINISHED")
                    self.assertEqual(
                        {a.path for a in client.list_artifacts(result["run_id"], "evaluation")},
                        {
                            "evaluation/champion-snapshot.json",
                            "evaluation/report.json",
                            "evaluation/input-snapshot.json",
                            "evaluation/test-set-manifest.json",
                        },
                    )
                    candidate = {key: result[key] for key in ("run_id", "model_uri", "git_commit")}
                    snapshot = json.loads(args.input_snapshot.read_text())
                    with patch.object(train, "prepare_data", side_effect=AssertionError("Preprocessing repeated")):
                        features, target = load_test_set(
                            args.test_set_directory, result["test_set_manifest"], candidate, snapshot
                        )
                    self.assertEqual(len(features), 20)
                    self.assertEqual(features.index.tolist(), report["test_set"]["test_row_indices"])
                    self.assertEqual(len(target), 20)
                    cli_report = root / "separate-process-report.json"
                    # The standalone evaluator has no dataset argument and shares only persisted inputs.
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "evaluation.evaluate",
                            "--training-result",
                            str(args.result_json),
                            "--input-snapshot",
                            str(args.input_snapshot),
                            "--test-set-directory",
                            str(args.test_set_directory),
                            "--report",
                            str(cli_report),
                            "--expected-commit",
                            "a" * 40,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(json.loads(cli_report.read_text()), report)

                    path.write_bytes(original_bytes)
                    snapshot_csv.write_bytes(original_bytes)
                    snapshot_csv.chmod(0o444)
                    args.n_estimators = 1
                    args.max_depth = 1
                    args.result_json = root / "rejected.json"
                    args.test_set_directory = root / "rejected-test-set"
                    args.test_set_directory.mkdir()
                    train.main(args)
                    rejected = json.loads(args.result_json.read_text())
                    rejected_report = evaluate.evaluate_training(
                        args.result_json,
                        args.input_snapshot,
                        args.test_set_directory,
                        root / "rejected-report.json",
                        "a" * 40,
                    )
                    self.assertFalse(rejected_report["passed"])
                    with (
                        patch.object(
                            evaluate,
                            "parse_arguments",
                            return_value=argparse.Namespace(
                                training_result=args.result_json,
                                input_snapshot=args.input_snapshot,
                                test_set_directory=args.test_set_directory,
                                report=root / "rejected-cli-report.json",
                                expected_commit="a" * 40,
                            ),
                        ),
                        self.assertRaises(SystemExit) as failure,
                    ):
                        evaluate.main()
                    self.assertIn("Evaluation rejected", str(failure.exception))
                    self.assertEqual(client.get_run(rejected["run_id"]).info.status, "FINISHED")
                    self.assertEqual(len(client.search_model_versions("name='isolated-model'")), 1)
                    self.assertEqual(str(client.get_model_version_by_alias("isolated-model", "champion").version), "1")


if __name__ == "__main__":
    unittest.main()
