import argparse
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from common.test_set_persistence import load_test_set
from common.training_snapshot import DATASET_FILENAME, SNAPSHOT_FILENAME, prepare_snapshot
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
                    evaluate_champion=False,
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
                    args.evaluate_champion = True
                    args.result_json = root / "candidate.json"
                    train_model = train.train_model

                    def mutate_after_training(*a, **kwargs):
                        model = train_model(*a, **kwargs)
                        path.write_text("changed CSV after training")
                        return model

                    with (
                        patch.object(train, "train_model", side_effect=mutate_after_training),
                        patch.object(train, "snapshot_champion", side_effect=AssertionError("Alias reread")),
                        patch.object(train, "test_set_identity", side_effect=AssertionError("Identity recalculated")),
                    ):
                        train.main(args)
                    result = json.loads(args.result_json.read_text())
                    report = result["evaluation"]
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

                    path.write_bytes(original_bytes)
                    args.n_estimators = 1
                    args.max_depth = 1
                    args.result_json = root / "rejected.json"
                    args.test_set_directory = root / "rejected-test-set"
                    args.test_set_directory.mkdir()
                    with self.assertRaises(SystemExit) as failure:
                        train.main(args)
                    self.assertEqual(failure.exception.code, 1)
                    rejected = json.loads(args.result_json.read_text())
                    self.assertFalse(rejected["evaluation"]["passed"])
                    self.assertEqual(client.get_run(rejected["run_id"]).info.status, "FINISHED")
                    self.assertEqual(len(client.search_model_versions("name='isolated-model'")), 1)
                    self.assertEqual(str(client.get_model_version_by_alias("isolated-model", "champion").version), "1")


if __name__ == "__main__":
    unittest.main()
