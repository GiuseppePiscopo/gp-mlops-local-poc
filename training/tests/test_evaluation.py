import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from common import evaluation
from sklearn.ensemble import RandomForestRegressor


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.features = pd.DataFrame({"first": [1.0, 2.0, 3.0], "second": [3.0, 4.0, 5.0]}, index=[4, 2, 8])
        self.target = pd.Series([1.0, 2.0, 4.0], index=self.features.index, name="target")
        self.client = MagicMock()
        self.client.get_model_version_by_alias.return_value = SimpleNamespace(
            status="READY", version="1", run_id="champion-run", source="models:/m-champion"
        )
        self.client.get_run.return_value.info.status = "FINISHED"
        self.candidate = {"run_id": "candidate-run", "model_uri": "models:/m-candidate", "git_commit": "a" * 40}
        self.identity = evaluation.test_set_identity("dataset-hash", self.features, self.target)

    def test_snapshot_is_resolved_once_and_alias_change_does_not_change_gate(self):
        champion = evaluation.snapshot_champion(self.client, "test-model")
        self.client.get_model_version_by_alias.return_value.version = "2"
        model = RandomForestRegressor(n_estimators=2, random_state=42, n_jobs=-1).fit(self.features, self.target)
        with patch.object(evaluation.mlflow.sklearn, "load_model", return_value=model) as load:
            report = evaluation.evaluate_candidate(self.candidate, champion, self.identity, self.features, self.target)
        self.client.get_model_version_by_alias.assert_called_once_with("test-model", "champion")
        self.assertEqual(
            [call.args[0] for call in load.call_args_list], ["models:/m-candidate", "models:/test-model/1"]
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["candidate_metrics"], report["champion_metrics"])
        self.assertEqual(report["champion"]["version"], "1")
        self.assertEqual(model.n_jobs, 1)

    def test_improvement_equality_and_regression_without_tolerance(self):
        champion = evaluation.snapshot_champion(self.client, "test-model")
        for rmse, passed in ((9.0, True), (10.0, True), (10.0000000001, False)):
            with (
                self.subTest(rmse=rmse),
                patch.object(evaluation, "evaluate_model", side_effect=[{"rmse": rmse}, {"rmse": 10.0}]) as evaluate,
            ):
                report = evaluation.evaluate_candidate(
                    self.candidate, champion, self.identity, self.features, self.target
                )
                self.assertEqual(report["passed"], passed)
                self.assertIs(evaluate.call_args_list[0].args[1], evaluate.call_args_list[1].args[1])
                self.assertIs(evaluate.call_args_list[0].args[2], evaluate.call_args_list[1].args[2])

    def test_unavailable_or_invalid_champion_fails_without_fallback(self):
        self.client.get_model_version_by_alias.side_effect = RuntimeError("Registry unavailable")
        with self.assertRaisesRegex(RuntimeError, "Registry unavailable"):
            evaluation.snapshot_champion(self.client, "test-model")
        self.client.get_model_version_by_alias.side_effect = None
        self.client.get_model_version_by_alias.return_value.status = "PENDING_REGISTRATION"
        with self.assertRaisesRegex(ValueError, "READY"):
            evaluation.snapshot_champion(self.client, "test-model")
        self.client.get_model_version_by_alias.return_value.status = "READY"
        self.client.get_run.return_value.info.status = "FAILED"
        with self.assertRaisesRegex(ValueError, "FINISHED"):
            evaluation.snapshot_champion(self.client, "test-model")

    def test_identity_records_order_values_and_preprocessing(self):
        identity = self.identity
        self.assertEqual(identity["test_row_indices"], [4, 2, 8])
        self.assertEqual(identity["test_rows"], 3)
        self.assertEqual(identity["random_seed"], 42)
        self.assertEqual(identity["test_size"], 0.2)
        self.assertEqual(
            identity["preprocessing_sha256"],
            hashlib.sha256(Path(evaluation.training_utils.__file__).read_bytes()).hexdigest(),
        )
        reordered = evaluation.test_set_identity("dataset-hash", self.features.iloc[::-1], self.target.iloc[::-1])
        self.assertNotEqual(identity["test_values_sha256"], reordered["test_values_sha256"])
        changed = evaluation.test_set_identity("dataset-hash", self.features, self.target + 1)
        self.assertNotEqual(identity["test_values_sha256"], changed["test_values_sha256"])

    def test_incompatible_features_load_errors_and_nonfinite_metrics_fail(self):
        with patch.object(evaluation.mlflow.sklearn, "load_model", side_effect=RuntimeError("Cannot load")):
            with self.assertRaisesRegex(RuntimeError, "Cannot load"):
                evaluation.evaluate_model("models:/bad", self.features, self.target)
        model = RandomForestRegressor(n_estimators=2).fit(self.features, self.target)
        with patch.object(evaluation.mlflow.sklearn, "load_model", return_value=model):
            with self.assertRaisesRegex(ValueError, "features"):
                evaluation.evaluate_model("models:/bad", self.features[["second", "first"]], self.target)
            for value in (np.nan, np.inf, -np.inf):
                with (
                    self.subTest(value=value),
                    patch.object(
                        evaluation.training_utils,
                        "calculate_metrics",
                        return_value={"rmse": value, "mae": 1.0, "r2": 0.8, "mape": 0.2},
                    ),
                ):
                    with self.assertRaisesRegex(ValueError, "non-finite"):
                        evaluation.evaluate_model("models:/bad", self.features, self.target)
            with patch.object(evaluation.training_utils, "calculate_metrics", return_value={}):
                with self.assertRaisesRegex(ValueError, "Missing"):
                    evaluation.evaluate_model("models:/bad", self.features, self.target)

    def test_summary_reports_the_exact_comparison(self):
        champion = evaluation.snapshot_champion(self.client, "test-model")
        metrics = {"rmse": 10.0, "mae": 8.0, "r2": 0.8, "mape": 0.2}
        with patch.object(evaluation, "evaluate_model", return_value=metrics):
            report = evaluation.evaluate_candidate(self.candidate, champion, self.identity, self.features, self.target)
        script = Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "report-training-result.py"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "result.json"
            evaluation_report = Path(directory) / "report.json"
            summary = Path(directory) / "summary"
            source.write_text(json.dumps(self.candidate))
            evaluation_report.write_text(json.dumps(report))
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--result",
                    str(source),
                    "--evaluation",
                    str(evaluation_report),
                    "--summary",
                    str(summary),
                ],
                check=True,
            )
            text = summary.read_text()
            self.assertIn("models:/m-candidate", text)
            self.assertIn("Champion run: `champion-run`", text)
            self.assertIn("Passed: `True`", text)
            self.assertIn("| rmse | 10.0 | 10.0 |", text)


if __name__ == "__main__":
    unittest.main()
