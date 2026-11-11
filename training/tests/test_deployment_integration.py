import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.test_set_persistence import snapshot_checksum
from common.training_snapshot import snapshot_champion

import mlflow

SCRIPT = Path(__file__).resolve().parents[2] / "ci-cd" / "scripts" / "check-deployment.py"
PROMOTION_SCRIPT = SCRIPT.with_name("promote-champion.py")


class DeploymentCheckIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mlops-deployment-integration-")))
        tracking_uri = f"sqlite:///{(self.root / 'mlflow.db').as_posix()}"
        previous_tracking_uri = mlflow.get_tracking_uri()
        self.enterContext(patch.dict(os.environ, {"MLFLOW_TRACKING_URI": tracking_uri}))
        self.addCleanup(mlflow.set_tracking_uri, previous_tracking_uri)
        mlflow.set_tracking_uri(tracking_uri)
        self.client = mlflow.MlflowClient(tracking_uri=tracking_uri)
        self.experiment = self.client.create_experiment("deployment", artifact_location=str(self.root / "artifacts"))
        self.client.create_registered_model("model")
        previous = self.create_version()
        self.target = self.create_version()
        for alias in ("champion", "baseline", "candidate"):
            self.client.set_registered_model_alias("model", alias, previous.version)

        # Persist only the source-run metadata consumed by the pre-deployment check.
        self.champion = snapshot_champion(self.client, "model")
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

    def run_check_cli(self, *, promote=False):
        extra_arguments = []
        if promote:
            verification = self.root / "verification.json"
            verification.write_text(
                json.dumps(
                    {
                        "status": "VERIFIED",
                        "registered_model": "model",
                        "model_version": str(self.target.version),
                        "model_uri": self.target.source,
                        "run_id": self.target.run_id,
                        "git_commit": "a" * 40,
                    }
                )
            )
            extra_arguments = ["--verification", str(verification)]
        return subprocess.run(
            [
                sys.executable,
                str(PROMOTION_SCRIPT if promote else SCRIPT),
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
                *extra_arguments,
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

    def test_promotion_cli_updates_champion_but_not_baseline_or_candidate(self):
        aliases = self.client.get_registered_model("model").aliases
        process = self.run_check_cli(promote=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "PROMOTED")
        self.assertEqual(result["previous_champion"], str(aliases["champion"]))
        expected = {**{key: str(value) for key, value in aliases.items()}, "champion": str(self.target.version)}
        actual = self.client.get_registered_model("model").aliases
        self.assertEqual({key: str(value) for key, value in actual.items()}, expected)
        self.assertEqual(len(self.client.search_model_versions("name='model'")), 2)


if __name__ == "__main__":
    unittest.main()
