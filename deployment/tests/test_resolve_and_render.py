import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
from mlflow.entities.model_registry import ModelVersion
from mlflow.exceptions import MlflowException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import resolve_and_render as resolver


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "rendered"
        deployment = Path(__file__).resolve().parents[1]
        configuration = yaml.safe_load(
            (deployment / "config" / "deployment-target.local.yaml").read_text(encoding="utf-8")
        )
        configuration["output"]["directory"] = str(self.output)
        self.config = self.directory / "target.yaml"
        self.config.write_text(yaml.safe_dump(configuration), encoding="utf-8")
        self.templates = deployment / "templates"
        self.client = Mock()
        self.client.get_model_version.return_value = self.model_version("2")
        self.client.get_model_version_by_alias.return_value = self.model_version("1")
        self.client.get_model_version_download_uri.return_value = "mlflow-artifacts:/1/models/m-fixed/artifacts"

    @staticmethod
    def model_version(version, status="READY", name="gp-mof-capstone-model"):
        return ModelVersion(
            name=name,
            version=version,
            creation_timestamp=0,
            run_id=f"run-{version}",
            source=f"models:/m-source-{version}",
            status=status,
        )

    def arguments(self, *selection):
        with patch.object(sys, "argv", ["resolver", "--config", str(self.config), *selection]):
            return resolver.parse_arguments()

    def execute(self, *selection):
        arguments = self.arguments(*selection)
        with (
            patch.object(resolver, "MlflowClient", return_value=self.client),
            patch.object(resolver, "DEFAULT_TEMPLATE_DIRECTORY", self.templates),
        ):
            return resolver.execute_deployment(arguments)

    def test_explicit_version_ignores_alias_and_records_exact_provenance(self):
        result = self.execute("--version", "2")

        self.client.get_model_version.assert_called_once_with(name="gp-mof-capstone-model", version="2")
        self.client.get_model_version_by_alias.assert_not_called()
        self.client.get_model_version_download_uri.assert_called_once_with(name="gp-mof-capstone-model", version="2")
        lock = json.loads(Path(result.resolution_lock_path).read_text(encoding="utf-8"))
        resolution = lock["resolution"]
        self.assertIsNone(resolution["requested_alias"])
        self.assertEqual(resolution["requested_version"], "2")
        self.assertEqual(resolution["resolved_version"], "2")
        self.assertEqual(resolution["source_run_id"], "run-2")
        self.assertEqual(resolution["model_version_source_uri"], "models:/m-source-2")
        self.assertEqual(resolution["resolved_storage_uri"], "s3://mlflow-artifacts/1/models/m-fixed/artifacts")
        self.assertEqual(lock["rendered_manifest_filename"], "gp-mof-capstone-model-v2.yaml")

        manifest = yaml.safe_load(Path(result.rendered_manifest_path).read_text(encoding="utf-8"))
        annotations = manifest["metadata"]["annotations"]
        self.assertEqual(annotations["mlops.gp-mof.dev/requested-version"], "2")
        self.assertNotIn("mlops.gp-mof.dev/requested-alias", annotations)
        self.assertEqual(manifest["spec"]["predictor"]["model"]["storageUri"], resolution["resolved_storage_uri"])

    def test_configured_and_overridden_alias_rendering_is_preserved(self):
        for selection, expected_alias in [((), "champion"), (("--alias", "candidate"), "candidate")]:
            with self.subTest(alias=expected_alias):
                self.client.reset_mock()
                result = self.execute(*selection)
                self.client.get_model_version.assert_not_called()
                self.client.get_model_version_by_alias.assert_called_once_with(
                    name="gp-mof-capstone-model", alias=expected_alias
                )
                lock = json.loads(Path(result.resolution_lock_path).read_text(encoding="utf-8"))
                self.assertEqual(lock["resolution"]["requested_alias"], expected_alias)
                self.assertIsNone(lock["resolution"]["requested_version"])
                manifest = yaml.safe_load(Path(result.rendered_manifest_path).read_text(encoding="utf-8"))
                annotations = manifest["metadata"]["annotations"]
                self.assertEqual(annotations["mlops.gp-mof.dev/requested-alias"], expected_alias)
                self.assertNotIn("mlops.gp-mof.dev/requested-version", annotations)

    def test_explicit_version_does_not_require_configured_alias(self):
        configuration = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        del configuration["model"]["alias"]
        self.config.write_text(yaml.safe_dump(configuration), encoding="utf-8")
        result = self.execute("--version", "2")
        self.assertEqual(result.resolution.resolved_version, "2")

    def test_explicit_version_remains_fixed_after_alias_moves_in_real_registry(self):
        tracking_uri = f"sqlite:///{(self.directory / 'mlflow.db').as_posix()}"
        client = resolver.MlflowClient(tracking_uri=tracking_uri)
        name = "gp-mof-capstone-model"
        client.create_registered_model(name)
        experiment_id = client.create_experiment("resolver", artifact_location=str(self.directory / "artifacts"))
        versions = []
        for index in (1, 2):
            run = client.create_run(experiment_id)
            version = client.create_model_version(
                name,
                source=f"mlflow-artifacts:/{experiment_id}/model-{index}/artifacts",
                run_id=run.info.run_id,
            )
            client.set_terminated(run.info.run_id)
            versions.append(version)
        original, newer = versions
        client.set_registered_model_alias(name, "candidate", original.version)
        client.set_registered_model_alias(name, "candidate", newer.version)

        configuration = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        configuration["mlflow"]["tracking_uri"] = tracking_uri
        configuration["model"]["alias"] = "candidate"
        self.config.write_text(yaml.safe_dump(configuration), encoding="utf-8")
        arguments = self.arguments("--version", str(original.version))
        with patch.object(resolver, "DEFAULT_TEMPLATE_DIRECTORY", self.templates):
            result = resolver.execute_deployment(arguments)

        lock = json.loads(Path(result.resolution_lock_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["resolution"]["requested_version"], str(original.version))
        self.assertEqual(lock["resolution"]["resolved_version"], str(original.version))
        self.assertEqual(lock["resolution"]["source_run_id"], original.run_id)
        self.assertEqual(lock["resolution"]["model_version_source_uri"], original.source)
        self.assertEqual(str(client.get_model_version_by_alias(name, "candidate").version), str(newer.version))

    def test_alias_and_version_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.arguments("--alias", "candidate", "--version", "2")
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists())

    def test_invalid_version_is_rejected_without_registry_lookup(self):
        for version in ("0", "-1", "01", "1.5", "latest", "", " 2", "2\n"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "positive integer"):
                self.execute("--version", version)
        self.client.get_model_version.assert_not_called()
        self.client.get_model_version_by_alias.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unavailable_version_fails_without_alias_fallback_or_outputs(self):
        for error in (MlflowException("Version not found"), MlflowException("Registry unavailable")):
            with self.subTest(error=str(error)):
                self.client.get_model_version.side_effect = error
                with self.assertRaises(MlflowException):
                    self.execute("--version", "2")
        self.client.get_model_version_by_alias.assert_not_called()
        self.client.get_model_version_download_uri.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_not_ready_or_mismatched_version_fails_before_download_and_render(self):
        for model in (
            self.model_version("2", "FAILED_REGISTRATION"),
            self.model_version("3"),
            self.model_version("2", name="another-model"),
        ):
            with self.subTest(name=model.name, version=model.version, status=model.status):
                self.client.get_model_version.return_value = model
                with self.assertRaises(ValueError):
                    self.execute("--version", "2")
        self.client.get_model_version_download_uri.assert_not_called()
        self.client.get_model_version_by_alias.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
