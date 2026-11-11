import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common.test_set_persistence import snapshot_checksum
from registration import register_candidate as registration


class ModelRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.candidate = {"run_id": "b" * 32, "model_uri": "models:/m-" + "c" * 32, "git_commit": "a" * 40}
        self.snapshot = {
            "git_commit": "a" * 40,
            "registered_model": "model",
            "mode": "relative",
            "champion": {"registered_model": "model", "version": "1"},
            "dataset": {"sha256": "dataset"},
        }
        self.manifest = {
            "candidate": copy.deepcopy(self.candidate),
            "input_snapshot_sha256": snapshot_checksum(self.snapshot),
            "test_set": {"dataset_sha256": "dataset", "test_values_sha256": "values"},
        }
        self.report = {
            "candidate": copy.deepcopy(self.candidate),
            "passed": True,
            "mode": "relative",
            "champion": self.snapshot["champion"],
            "test_set": self.manifest["test_set"],
        }
        self.training = {**self.candidate, "test_set_manifest": self.manifest}
        self.args = argparse.Namespace(
            training_result=self.root / "training.json",
            evaluation=self.root / "report.json",
            input_snapshot=self.root / "snapshot.json",
            registered_model="model",
            expected_commit="a" * 40,
            event_name="push",
            git_ref="refs/heads/main",
            result=self.root / "registration.json",
        )
        self.write_inputs()
        evidence = self.root / "evidence"
        evidence.mkdir()
        for name, value in (
            ("report.json", self.report),
            ("input-snapshot.json", self.snapshot),
            ("test-set-manifest.json", self.manifest),
        ):
            (evidence / name).write_text(json.dumps(value))
        self.client = MagicMock()
        self.client.get_run.return_value = SimpleNamespace(
            info=SimpleNamespace(status="FINISHED"),
            data=SimpleNamespace(
                tags={
                    "git.commit": "a" * 40,
                    "dataset.sha256": "dataset",
                    "evaluation.passed": "true",
                }
            ),
        )
        self.source = self.candidate["model_uri"]
        self.client.get_logged_model.return_value = SimpleNamespace(
            source_run_id=self.candidate["run_id"], artifact_location=self.source
        )
        self.client.download_artifacts.side_effect = lambda run_id, artifact, directory: str(
            evidence / Path(artifact).name
        )
        self.client.get_model_version.return_value = SimpleNamespace(
            status="READY", run_id=self.candidate["run_id"], source=self.source
        )
        self.client.get_model_version_by_alias.return_value.version = "2"
        self.enterContext(patch.object(registration.mlflow, "MlflowClient", return_value=self.client))
        self.register = self.enterContext(patch.object(registration.mlflow, "register_model"))
        self.register.return_value.version = 2

    def write_inputs(self):
        self.args.training_result.write_text(json.dumps(self.training))
        self.args.evaluation.write_text(json.dumps(self.report))
        self.args.input_snapshot.write_text(json.dumps(self.snapshot))

    def test_exact_ready_version_is_registered_then_assigned_only_to_candidate(self):
        result = registration.register_candidate(self.args)
        self.assertEqual(
            result,
            {
                **self.candidate,
                "registered_model": "model",
                "model_version": "2",
                "status": "READY",
            },
        )
        self.assertEqual(json.loads(self.args.result.read_text()), result)
        self.register.assert_called_once_with(
            self.candidate["model_uri"],
            "model",
            await_registration_for=120,
            tags={"git.commit": "a" * 40, "evaluation.mode": "relative"},
        )
        self.client.set_registered_model_alias.assert_called_once_with("model", "candidate", "2")
        self.assertEqual(self.client.download_artifacts.call_count, 3)

    def test_pr_or_non_main_push_cannot_register_even_with_a_passed_report(self):
        for event, ref in (("pull_request", "refs/pull/1/merge"), ("push", "refs/heads/feature")):
            with self.subTest(event=event):
                self.args.event_name, self.args.git_ref = event, ref
                with self.assertRaisesRegex(ValueError, "push to main or a manual"):
                    registration.register_candidate(self.args)
        self.client.get_run.assert_not_called()
        self.register.assert_not_called()

    def test_initial_model_registration_cannot_be_requested_by_push(self):
        self.snapshot.update(mode="bootstrap", champion=None)
        self.report.update(mode="bootstrap", champion=None)
        self.manifest["input_snapshot_sha256"] = snapshot_checksum(self.snapshot)
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "requires a manual"):
            registration.register_candidate(self.args)
        self.register.assert_not_called()
        self.client.get_run.assert_not_called()

    def test_failed_or_mismatched_gate_never_creates_a_version(self):
        original = copy.deepcopy(self.report)
        for scenario in ("failed", "string_outcome", "candidate", "test_set", "persisted_report"):
            with self.subTest(scenario=scenario):
                self.report = copy.deepcopy(original)
                if scenario == "failed":
                    self.report["passed"] = False
                elif scenario == "string_outcome":
                    self.report["passed"] = "true"
                elif scenario == "candidate":
                    self.report["candidate"]["run_id"] = "d" * 32
                elif scenario == "test_set":
                    self.report["test_set"]["dataset_sha256"] = "changed"
                else:
                    self.report["extra"] = "not in source artifact"
                self.write_inputs()
                with self.assertRaises(ValueError):
                    registration.register_candidate(self.args)
                self.assertFalse(self.args.result.exists())
        self.register.assert_not_called()
        self.client.set_registered_model_alias.assert_not_called()

    def test_unfinished_run_or_wrong_logged_model_blocks_registration(self):
        for scenario in ("run_status", "gate_tag", "model_owner"):
            with self.subTest(scenario=scenario):
                if scenario == "run_status":
                    self.client.get_run.return_value.info.status = "FAILED"
                elif scenario == "gate_tag":
                    self.client.get_run.return_value.data.tags["evaluation.passed"] = "false"
                else:
                    self.client.get_logged_model.return_value.source_run_id = "another-run"
                with self.assertRaises(ValueError):
                    registration.register_candidate(self.args)
                self.client.get_run.return_value.info.status = "FINISHED"
                self.client.get_run.return_value.data.tags["evaluation.passed"] = "true"
        self.register.assert_not_called()

    def test_version_verification_failure_never_changes_alias(self):
        for scenario in ("status", "source", "run_id"):
            with self.subTest(scenario=scenario):
                verified = SimpleNamespace(status="READY", source=self.source, run_id=self.candidate["run_id"])
                setattr(verified, scenario, "unexpected")
                self.client.get_model_version.return_value = verified
                with self.assertRaisesRegex(ValueError, "Model Version model/2"):
                    registration.register_candidate(self.args)
                self.assertFalse(self.args.result.exists())
        self.client.set_registered_model_alias.assert_not_called()

    def test_alias_failure_surfaces_created_version_without_a_success_receipt(self):
        self.client.set_registered_model_alias.side_effect = RuntimeError("Registry write failed")
        with patch("builtins.print") as message:
            with self.assertRaisesRegex(RuntimeError, "Registry write failed"):
                registration.register_candidate(self.args)
        self.assertIn("model/2", message.call_args.args[0])
        self.assertFalse(self.args.result.exists())
        self.client.delete_model_version.assert_not_called()

    def test_existing_receipt_is_not_reused_or_overwritten(self):
        self.args.result.write_text("existing")
        with self.assertRaisesRegex(ValueError, "already exists"):
            registration.register_candidate(self.args)
        self.register.assert_not_called()
        self.assertEqual(self.args.result.read_text(), "existing")

    def test_alias_readback_mismatch_blocks_receipt(self):
        self.client.get_model_version_by_alias.return_value.version = "3"
        with self.assertRaisesRegex(ValueError, "candidate alias verification failed"):
            registration.register_candidate(self.args)
        self.assertFalse(self.args.result.exists())


if __name__ == "__main__":
    unittest.main()
