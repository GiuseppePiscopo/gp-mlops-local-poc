import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
from common.test_set_persistence import save_test_set
from evaluation import evaluate


class EvaluateTrainingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidate = {"run_id": "candidate-run", "model_uri": "models:/m-candidate", "git_commit": "a" * 40}
        self.snapshot = {
            "git_commit": "a" * 40,
            "dataset": {"sha256": "dataset-hash"},
            "champion": {"registered_model": "model", "version": "1", "model_uri": "models:/model/1"},
        }
        self.test_set = self.root / "test-set"
        self.test_set.mkdir()
        features = pd.DataFrame({"first": [1.0, 2.0]})
        target = pd.Series([2, 4], name="target")
        manifest = save_test_set(self.test_set, features, target, self.candidate, self.snapshot)
        self.result = {**self.candidate, "test_set_manifest": manifest}
        self.training_result = self.root / "training.json"
        self.training_result.write_text(json.dumps(self.result))
        self.input_snapshot = self.root / "snapshot.json"
        self.input_snapshot.write_text(json.dumps(self.snapshot))
        self.report = self.root / "report.json"
        self.client = MagicMock()
        self.client.get_run.return_value = SimpleNamespace(
            info=SimpleNamespace(status="FINISHED"),
            data=SimpleNamespace(tags={"git.commit": "a" * 40, "dataset.sha256": "dataset-hash"}),
        )
        self.client.get_logged_model.return_value.source_run_id = "candidate-run"
        self.enterContext(patch.object(evaluate.mlflow, "MlflowClient", return_value=self.client))

    def run_evaluation(self):
        return evaluate.evaluate_training(
            self.training_result, self.input_snapshot, self.test_set, self.report, "a" * 40
        )

    def test_bad_commit_run_or_model_blocks_evaluation(self):
        for scenario in ("commit", "run_status", "run_provenance", "model_owner"):
            with self.subTest(scenario=scenario):
                if scenario == "commit":
                    self.training_result.write_text(json.dumps({**self.result, "git_commit": "b" * 40}))
                elif scenario == "run_status":
                    self.client.get_run.return_value.info.status = "FAILED"
                elif scenario == "run_provenance":
                    self.client.get_run.return_value.data.tags["dataset.sha256"] = "changed"
                else:
                    self.client.get_logged_model.return_value.source_run_id = "another-run"
                with patch.object(evaluate, "evaluate_candidate") as gate:
                    with self.assertRaises(ValueError):
                        self.run_evaluation()
                    gate.assert_not_called()
                self.assertFalse(self.report.exists())
                self.training_result.write_text(json.dumps(self.result))
                self.client.get_run.return_value.info.status = "FINISHED"
                self.client.get_run.return_value.data.tags["dataset.sha256"] = "dataset-hash"

    def test_test_set_tampering_blocks_model_loading_and_report(self):
        (self.test_set / "test-data.json").write_text("{}")
        with patch.object(evaluate, "evaluate_candidate") as gate:
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.run_evaluation()
            gate.assert_not_called()
        self.assertFalse(self.report.exists())
        self.client.log_dict.assert_not_called()

    def test_gate_error_is_not_converted_to_success(self):
        with patch.object(evaluate, "evaluate_candidate", side_effect=RuntimeError("Cannot load champion")):
            with self.assertRaisesRegex(RuntimeError, "Cannot load champion"):
                self.run_evaluation()
        self.assertFalse(self.report.exists())
        self.client.set_tag.assert_not_called()

    def test_rejection_report_is_saved_without_reopening_finished_run(self):
        report = {"candidate": self.candidate, "passed": False}
        with patch.object(evaluate, "evaluate_candidate", return_value=report):
            self.assertEqual(self.run_evaluation(), report)
        self.assertEqual(json.loads(self.report.read_text()), report)
        self.client.set_tag.assert_called_once_with("candidate-run", "evaluation.passed", "false")
        self.client.get_model_version_by_alias.assert_not_called()
        self.client.create_model_version.assert_not_called()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_evaluation()


if __name__ == "__main__":
    unittest.main()
