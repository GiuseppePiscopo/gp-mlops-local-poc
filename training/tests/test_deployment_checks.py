import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common.test_set_persistence import snapshot_checksum
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import PERMISSION_DENIED, RESOURCE_DOES_NOT_EXIST

SCRIPT = Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "check-deployment.py"
SPEC = importlib.util.spec_from_file_location("check_deployment", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class DeploymentCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = argparse.Namespace(
            run_id="b" * 32,
            model_uri="models:/m-" + "c" * 32,
            expected_commit="a" * 40,
            registered_model="model",
            model_version="2",
            evaluation_mode="relative",
            event_name="push",
            git_ref="refs/heads/main",
        )
        self.candidate = {
            "run_id": self.args.run_id,
            "model_uri": self.args.model_uri,
            "git_commit": self.args.expected_commit,
        }
        self.champion = {
            "registered_model": "model",
            "version": "1",
            "run_id": "d" * 32,
            "source": "models:/m-" + "e" * 32,
            "model_uri": "models:/model/1",
        }
        self.snapshot = {
            "git_commit": self.args.expected_commit,
            "mode": "relative",
            "registered_model": "model",
            "champion": copy.deepcopy(self.champion),
            "dataset": {"sha256": "dataset"},
        }
        self.manifest = {
            "candidate": self.candidate,
            "input_snapshot_sha256": snapshot_checksum(self.snapshot),
            "test_set": {"dataset_sha256": "dataset"},
        }
        self.report = {
            "candidate": self.candidate,
            "mode": "relative",
            "champion": copy.deepcopy(self.champion),
            "passed": True,
            "test_set": self.manifest["test_set"],
        }
        self.write_evidence()
        self.client = MagicMock()
        self.client.get_model_version.return_value = SimpleNamespace(
            name="model",
            version="2",
            status="READY",
            run_id=self.args.run_id,
            source=self.args.model_uri,
            tags={"git.commit": self.args.expected_commit, "evaluation.mode": "relative"},
        )
        self.client.get_run.return_value = SimpleNamespace(
            info=SimpleNamespace(status="FINISHED"),
            data=SimpleNamespace(
                tags={"git.commit": self.args.expected_commit, "evaluation.passed": "true", "dataset.sha256": "dataset"}
            ),
        )
        self.client.get_logged_model.return_value.source_run_id = self.args.run_id
        self.client.get_registered_model.return_value.aliases = {"champion": "1", "candidate": "99"}
        self.client.get_model_version_by_alias.return_value = SimpleNamespace(
            version="1", status="READY", run_id=self.champion["run_id"], source=self.champion["source"]
        )
        self.client.download_artifacts.side_effect = lambda run_id, artifact, directory: str(
            self.root / Path(artifact).name
        )
        self.enterContext(patch.object(checker.mlflow, "MlflowClient", return_value=self.client))

    def write_evidence(self):
        for name, value in (
            ("report", self.report),
            ("input-snapshot", self.snapshot),
            ("test-set-manifest", self.manifest),
        ):
            (self.root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

    def use_bootstrap(self):
        self.args.evaluation_mode = "bootstrap"
        self.args.event_name = "workflow_dispatch"
        self.snapshot.update(mode="bootstrap", champion=None)
        self.report.update(mode="bootstrap", champion=None)
        self.manifest["input_snapshot_sha256"] = snapshot_checksum(self.snapshot)
        self.client.get_model_version.return_value.tags["evaluation.mode"] = "bootstrap"
        self.write_evidence()

    def test_unchanged_champion_accepts_exact_version_without_resolving_candidate_or_writes(self):
        result = checker.check_deployment(self.args)
        self.assertEqual(result["champion"], self.champion)
        self.assertEqual(result["model_version"], "2")
        self.client.get_model_version.assert_called_once_with("model", "2")
        self.client.get_model_version_by_alias.assert_called_once_with("model", "champion")
        self.client.set_registered_model_alias.assert_not_called()
        self.client.create_model_version.assert_not_called()
        self.assertEqual(self.client.download_artifacts.call_count, 3)

    def test_changed_champion_identity_requires_new_execution(self):
        version = self.client.get_model_version_by_alias.return_value
        for field in ("version", "run_id", "source"):
            original = getattr(version, field)
            with self.subTest(field=field):
                setattr(version, field, "changed")
                with self.assertRaisesRegex(ValueError, "Obsolete deployment:.*Start a new workflow"):
                    checker.check_deployment(self.args)
                setattr(version, field, original)
        self.client.set_registered_model_alias.assert_not_called()

    def test_missing_champion_or_registered_model_rejects_relative_release(self):
        self.client.get_registered_model.return_value.aliases = {}
        with self.assertRaisesRegex(ValueError, "current champion=null"):
            checker.check_deployment(self.args)
        self.client.get_registered_model.side_effect = MlflowException("Missing", error_code=RESOURCE_DOES_NOT_EXIST)
        with self.assertRaisesRegex(ValueError, "current champion=null"):
            checker.check_deployment(self.args)
        self.client.get_model_version_by_alias.assert_not_called()

    def test_bootstrap_accepts_absence_but_rejects_champion_appearing_during_approval(self):
        self.use_bootstrap()
        self.client.get_registered_model.return_value.aliases = {"candidate": "2"}
        self.assertIsNone(checker.check_deployment(self.args)["champion"])
        self.client.get_registered_model.return_value.aliases["champion"] = "1"
        with self.assertRaisesRegex(ValueError, "Obsolete deployment:.*Start an ordinary workflow"):
            checker.check_deployment(self.args)
        self.client.set_registered_model_alias.assert_not_called()

    def test_registry_failures_are_not_treated_as_absence(self):
        for mode in ("relative", "bootstrap"):
            if mode == "bootstrap":
                self.use_bootstrap()
            for error in (
                MlflowException("Access denied", error_code=PERMISSION_DENIED),
                RuntimeError("Network unavailable"),
            ):
                with self.subTest(mode=mode, error=str(error)):
                    self.client.get_registered_model.side_effect = error
                    with self.assertRaises(type(error)):
                        checker.check_deployment(self.args)
        self.client.set_registered_model_alias.assert_not_called()

    def test_missing_version_or_evidence_fails_without_fallback(self):
        for method in (self.client.get_model_version, self.client.download_artifacts):
            original = method.side_effect
            with self.subTest(method=method):
                method.side_effect = MlflowException("Missing", error_code=RESOURCE_DOES_NOT_EXIST)
                with self.assertRaises(MlflowException):
                    checker.check_deployment(self.args)
                method.side_effect = original
        self.client.get_model_version_by_alias.assert_not_called()

    def test_ineligible_events_and_invalid_identifiers_fail_before_registry_reads(self):
        for field, invalid in (
            ("event_name", "pull_request"),
            ("git_ref", "refs/heads/feature"),
            ("model_version", "0"),
            ("run_id", "not-a-run"),
            ("expected_commit", "short"),
            ("model_uri", "models:/model/candidate"),
        ):
            original = getattr(self.args, field)
            with self.subTest(field=field):
                setattr(self.args, field, invalid)
                with self.assertRaises(ValueError):
                    checker.check_deployment(self.args)
                setattr(self.args, field, original)
        self.use_bootstrap()
        self.args.event_name = "push"
        with self.assertRaisesRegex(ValueError, "manual"):
            checker.check_deployment(self.args)
        self.client.get_model_version.assert_not_called()

    def test_version_run_and_logged_model_provenance_are_required(self):
        mutations = (
            (self.client.get_model_version.return_value, "status", "FAILED_REGISTRATION"),
            (self.client.get_model_version.return_value, "source", "different-source"),
            (self.client.get_model_version.return_value, "run_id", "different-run"),
            (self.client.get_run.return_value.info, "status", "FAILED"),
            (self.client.get_logged_model.return_value, "source_run_id", "different-run"),
        )
        for target, field, invalid in mutations:
            original = getattr(target, field)
            with self.subTest(field=field, invalid=invalid):
                setattr(target, field, invalid)
                with self.assertRaises(ValueError):
                    checker.check_deployment(self.args)
                setattr(target, field, original)
        self.client.download_artifacts.assert_not_called()

    def test_mismatched_evidence_or_failed_gate_is_rejected_before_alias_read(self):
        for document, key, invalid in (
            (self.report, "passed", False),
            (self.report, "passed", "true"),
            (self.report, "candidate", {}),
            (self.report, "champion", None),
            (self.snapshot, "git_commit", "f" * 40),
            (self.manifest, "input_snapshot_sha256", "different"),
        ):
            original = document[key]
            with self.subTest(key=key):
                document[key] = invalid
                self.write_evidence()
                with self.assertRaises(ValueError):
                    checker.check_deployment(self.args)
                document[key] = original
        self.client.get_registered_model.assert_not_called()


class DeploymentCheckIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mlops-deployment-integration-")))
        tracking_uri = f"sqlite:///{(self.root / 'mlflow.db').as_posix()}"
        previous_tracking_uri = checker.mlflow.get_tracking_uri()
        self.enterContext(patch.dict(os.environ, {"MLFLOW_TRACKING_URI": tracking_uri}))
        self.addCleanup(checker.mlflow.set_tracking_uri, previous_tracking_uri)
        checker.mlflow.set_tracking_uri(tracking_uri)
        self.client = checker.mlflow.MlflowClient(tracking_uri=tracking_uri)
        self.experiment = self.client.create_experiment("deployment", artifact_location=str(self.root / "artifacts"))
        self.client.create_registered_model("model")
        previous = self.create_version()
        self.target = self.create_version()
        for alias in ("champion", "baseline", "candidate"):
            self.client.set_registered_model_alias("model", alias, previous.version)

        # Persist only the source-run metadata consumed by the pre-deployment check.
        self.champion = checker.snapshot_champion(self.client, "model")
        candidate = {"run_id": self.target.run_id, "model_uri": self.target.source, "git_commit": "a" * 40}
        snapshot = {
            "git_commit": "a" * 40,
            "mode": "relative",
            "registered_model": "model",
            "champion": self.champion,
            "dataset": {"sha256": "dataset"},
        }
        manifest = {
            "candidate": candidate,
            "input_snapshot_sha256": snapshot_checksum(snapshot),
            "test_set": {"dataset_sha256": "dataset"},
        }
        report = {
            "candidate": candidate,
            "mode": "relative",
            "champion": self.champion,
            "passed": True,
            "test_set": manifest["test_set"],
        }
        for name, value in (("report", report), ("input-snapshot", snapshot), ("test-set-manifest", manifest)):
            self.client.log_dict(self.target.run_id, value, f"evaluation/{name}.json")

    def create_version(self):
        run = self.client.create_run(
            self.experiment,
            tags={"git.commit": "a" * 40, "evaluation.passed": "true", "dataset.sha256": "dataset"},
        )
        logged = self.client.create_logged_model(self.experiment, source_run_id=run.info.run_id)
        version = self.client.create_model_version(
            "model",
            source=f"models:/{logged.model_id}",
            run_id=run.info.run_id,
            tags={"git.commit": "a" * 40, "evaluation.mode": "relative"},
        )
        self.client.set_terminated(run.info.run_id)
        return version

    def run_check_cli(self):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--run-id",
                self.target.run_id,
                "--model-uri",
                self.target.source,
                "--expected-commit",
                "a" * 40,
                "--registered-model",
                "model",
                "--model-version",
                str(self.target.version),
                "--evaluation-mode",
                "relative",
                "--event-name",
                "push",
                "--git-ref",
                "refs/heads/main",
            ],
            capture_output=True,
            text=True,
        )

    def test_cli_accepts_fixed_target_without_changing_registry(self):
        aliases = self.client.get_registered_model("model").aliases
        process = self.run_check_cli()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["champion"], self.champion)
        self.assertEqual(result["model_version"], str(self.target.version))
        self.assertEqual(result["model_uri"], self.target.source)
        self.assertEqual(self.client.get_registered_model("model").aliases, aliases)
        self.assertEqual(len(self.client.search_model_versions("name='model'")), 2)

    def test_cli_rejects_champion_changed_after_evaluation(self):
        self.client.set_registered_model_alias("model", "champion", self.target.version)
        process = self.run_check_cli()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Obsolete deployment", process.stderr)
        self.assertIn("Start a new workflow execution", process.stderr)
        self.assertIn('"version": "1"', process.stderr)
        self.assertIn('"version": "2"', process.stderr)
        self.assertEqual(
            str(self.client.get_model_version_by_alias("model", "champion").version), str(self.target.version)
        )


if __name__ == "__main__":
    unittest.main()
