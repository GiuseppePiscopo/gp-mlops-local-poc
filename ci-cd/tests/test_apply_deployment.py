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
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply-deployment.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("apply_deployment", SCRIPT)
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class ApplyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.output = self.root / "deployment"
        self.args = argparse.Namespace(
            repository=self.repository,
            output_directory=self.output,
            registered_model="model",
            model_version="2",
            model_uri="models:/m-" + "b" * 32,
            run_id="c" * 32,
            expected_commit="a" * 40,
            cluster="mlops-test",
            context="kind-mlops-test",
            network="mlops-local-network",
            summary=self.root / "summary",
        )
        self.images = {
            component: {
                "image": f"image-{component}:git-" + "a" * 40,
                "image_id": "sha256:" + digit * 64,
                "git_commit": "a" * 40,
            }
            for component, digit in (("deployment", "d"), ("serving", "e"))
        }
        self.resolution = {
            "registered_model": "model",
            "requested_alias": None,
            "requested_version": "2",
            "resolved_version": "2",
            "source_run_id": self.args.run_id,
            "model_version_source_uri": self.args.model_uri,
            "model_version_download_uri": "mlflow-artifacts:/models/m-target/artifacts",
            "resolved_storage_uri": "s3://mlflow-artifacts/models/m-target/artifacts",
            "status": "READY",
        }
        self.documents = {
            "00-namespace.yaml": {"kind": "Namespace", "metadata": {"name": "serving"}},
            "10-service-account.yaml": {
                "kind": "ServiceAccount",
                "metadata": {"name": "reader", "namespace": "serving"},
            },
            "20-serving-runtime.yaml": {
                "kind": "ServingRuntime",
                "metadata": {"name": "runtime", "namespace": "serving"},
                "spec": {"containers": [{"image": self.images["serving"]["image"]}]},
            },
            "model-v2.yaml": {
                "kind": "InferenceService",
                "metadata": {
                    "name": "model",
                    "namespace": "serving",
                    "annotations": {
                        f"mlops.gp-mof.dev/{key.replace('_', '-')}": value
                        for key, value in self.resolution.items()
                        if key not in ("requested_alias", "status")
                    },
                },
                "spec": {
                    "predictor": {
                        "serviceAccountName": "reader",
                        "model": {
                            "runtime": "runtime",
                            "storageUri": self.resolution["resolved_storage_uri"],
                            "env": [{"name": "MODEL_NAME", "value": "model"}, {"name": "MODEL_VERSION", "value": "2"}],
                        },
                    }
                },
            },
        }
        self.documents["model-v2.yaml"]["metadata"]["annotations"]["serving.kserve.io/secretName"] = "model-storage"
        self.calls = []
        self.nodes = ["mlops-test-control-plane"]
        self.cluster_nodes = list(self.nodes)
        self.networks = {}
        self.platform_digest = "sha256:" + "f" * 64
        self.config_digest = "sha256:" + "a" * 64
        self.loaded_id = self.config_digest
        self.failure = None
        self.lock_mismatch = False
        self.enterContext(patch.object(deployment, "command", side_effect=self.fake_command))
        self.build = self.enterContext(
            patch.object(
                deployment,
                "prepare_image",
                side_effect=lambda action, repository, component, override: copy.deepcopy(self.images[component]),
            )
        )
        self.enterContext(patch.object(deployment.os, "getuid", return_value=1000, create=True))
        self.enterContext(patch.object(deployment.os, "getgid", return_value=1000, create=True))

    def fake_command(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        output = self.args.output_directory
        if self.failure is not None and self.failure(arguments):
            raise subprocess.CalledProcessError(1, arguments, stderr="Injected failure")
        if arguments[0] == "git":
            return self.args.expected_commit
        if arguments[:2] == ["docker", "run"]:
            self.assertIn(self.images["deployment"]["image_id"], arguments)
            self.assertEqual(arguments[-4:], ["--registered-model", "model", "--version", "2"])
            (output / "model-v2.yaml").write_text("rendered model", encoding="utf-8")
            (output / "model-v2.resolution.json").write_text(
                json.dumps(
                    {
                        "rendered_manifest_filename": "wrong.yaml" if self.lock_mismatch else "model-v2.yaml",
                        "resolution": self.resolution,
                    }
                ),
                encoding="utf-8",
            )
            return json.dumps(
                {
                    "resolution": self.resolution,
                    "rendered_manifest_path": "/output/model-v2.yaml",
                    "resolution_lock_path": "/output/model-v2.resolution.json",
                }
            )
        if arguments[0] == sys.executable:
            (output / "20-serving-runtime.yaml").write_text("rendered runtime", encoding="utf-8")
            return json.dumps({"manifest": str(output / "20-serving-runtime.yaml"), **self.images["serving"]})
        if arguments[0] == "kubectl":
            self.assertEqual(arguments[1:3], ["--context", "kind-mlops-test"])
            if "--dry-run=client" in arguments:
                return json.dumps(self.documents[Path(arguments[arguments.index("-f") + 1]).name])
            if arguments[3:5] == ["get", "nodes"]:
                return json.dumps({"items": [{"metadata": {"name": node}} for node in self.cluster_nodes]})
            return "applied"
        if arguments[:3] == ["kind", "get", "nodes"]:
            return "\n".join(self.nodes)
        if arguments[:2] == ["kind", "load"]:
            return "loaded"
        if arguments[:2] == ["docker", "inspect"]:
            return json.dumps(self.networks)
        if arguments[:3] == ["docker", "network", "connect"]:
            return ""
        if arguments[:3] == ["docker", "image", "inspect"]:
            self.assertIn("--platform", arguments)
            return json.dumps({"digest": self.platform_digest})
        if arguments[:2] == ["docker", "exec"]:
            if arguments[3] == "ctr":
                self.assertEqual(arguments[-1], self.platform_digest)
                return json.dumps({"config": {"digest": self.config_digest}})
            return json.dumps({"status": {"id": self.loaded_id}})
        if arguments[0] == "bash":
            if "check-storage-config" in arguments:
                return "serving\nreader\nmodel-storage"
            self.assertEqual(kwargs["env"]["KUBERNETES_CONTEXT"], "kind-mlops-test")
            return "S3 storage Secret applied."
        self.fail(f"Unexpected command: {arguments}")

    def assert_no_model_apply(self):
        self.assertFalse(
            any(args[0] == "kubectl" and args[3] == "apply" for args, _ in self.calls),
            self.calls,
        )
        self.assertFalse((self.args.output_directory / "deployment-result.json").exists())
        self.assertFalse(self.args.summary.exists())

    def test_exact_target_is_applied_after_validation_and_verified_image_load(self):
        result = deployment.apply_deployment(self.args)
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["model_version"], "2")
        self.assertEqual(result["images"], self.images)
        self.assertEqual(
            result["kind_image"],
            {"platform_manifest_digest": self.platform_digest, "node_image_ids": {self.nodes[0]: self.config_digest}},
        )
        self.assertEqual(json.loads((self.output / "deployment-result.json").read_text()), result)
        self.assertIn(
            "Readiness, inference and champion promotion follow in the verification step.", self.args.summary.read_text()
        )
        commands = [args for args, _ in self.calls]
        self.assertIn(
            ["kind", "load", "docker-image", self.images["serving"]["image"], "--name", "mlops-test"], commands
        )
        self.assertIn(["docker", "network", "connect", self.args.network, self.nodes[0]], commands)
        applied = [
            Path(args[-1]).name
            for args in commands
            if args[0] == "kubectl" and args[3] == "apply" and "--dry-run=server" not in args
        ]
        self.assertEqual(
            applied, ["00-namespace.yaml", "10-service-account.yaml", "20-serving-runtime.yaml", "model-v2.yaml"]
        )
        self.assertEqual([call.args[2] for call in self.build.call_args_list], ["deployment", "serving"])

    def test_wrong_resolution_or_lock_stops_before_cluster_changes(self):
        for scenario in ("version", "run", "source", "lock"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as output:
                self.args.output_directory = Path(output) / "new"
                if scenario == "lock":
                    self.lock_mismatch = True
                else:
                    key = {"version": "resolved_version", "run": "source_run_id", "source": "model_version_source_uri"}[
                        scenario
                    ]
                    original = self.resolution[key]
                    self.resolution[key] = "wrong"
                with self.assertRaises(ValueError):
                    deployment.apply_deployment(self.args)
                self.assert_no_model_apply()
                if scenario != "lock":
                    self.resolution[key] = original

    def test_invalid_manifest_identity_or_logging_version_stops_before_apply(self):
        for scenario in ("storage", "namespace", "logging_version", "annotation", "runtime_image"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as output:
                self.args.output_directory = Path(output) / "new"
                original = copy.deepcopy(self.documents)
                service = self.documents["model-v2.yaml"]
                if scenario == "storage":
                    service["spec"]["predictor"]["model"]["storageUri"] = "s3://another/model"
                elif scenario == "namespace":
                    service["metadata"]["namespace"] = "wrong"
                elif scenario == "logging_version":
                    service["spec"]["predictor"]["model"]["env"][1]["value"] = "1"
                elif scenario == "annotation":
                    service["metadata"]["annotations"]["mlops.gp-mof.dev/resolved-version"] = "1"
                else:
                    self.documents["20-serving-runtime.yaml"]["spec"]["containers"][0]["image"] = "unverified:latest"
                with self.assertRaises(ValueError):
                    deployment.apply_deployment(self.args)
                self.assert_no_model_apply()
                self.documents = original

    def test_wrong_cluster_or_loaded_image_stops_before_apply(self):
        for scenario in ("cluster", "image"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as output:
                self.args.output_directory = Path(output) / "new"
                self.cluster_nodes = ["wrong-node"] if scenario == "cluster" else list(self.nodes)
                self.loaded_id = "sha256:wrong" if scenario == "image" else self.config_digest
                with self.assertRaises(ValueError):
                    deployment.apply_deployment(self.args)
                self.assert_no_model_apply()

    def test_build_load_secret_validation_and_apply_errors_never_publish_success(self):
        for stage in ("build", "load", "secret", "dry-run", "apply"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as output:
                self.args.output_directory = Path(output) / "new"
                self.failure = {
                    "load": lambda args: args[:2] == ["kind", "load"],
                    "secret": lambda args: args[0] == "bash",
                    "dry-run": lambda args: "--dry-run=server" in args and Path(args[-1]).name == "model-v2.yaml",
                    "apply": lambda args: (
                        args[0] == "kubectl" and args[3] == "apply" and Path(args[-1]).name == "model-v2.yaml"
                    ),
                }.get(stage)
                if stage == "build":
                    self.build.side_effect = subprocess.CalledProcessError(1, ["docker", "build"])
                with self.assertRaises(subprocess.CalledProcessError):
                    deployment.apply_deployment(self.args)
                self.assertFalse((self.args.output_directory / "deployment-result.json").exists())
                self.assertFalse(self.args.summary.exists())
                if stage == "dry-run":
                    self.assertFalse(
                        any(
                            args[0] == "kubectl"
                            and args[3] == "apply"
                            and "--dry-run=server" not in args
                            and Path(args[-1]).name in ("20-serving-runtime.yaml", "model-v2.yaml")
                            for args, _ in self.calls
                        )
                    )
                self.build.side_effect = lambda action, repository, component, override: copy.deepcopy(
                    self.images[component]
                )

    def test_existing_or_in_checkout_output_is_rejected(self):
        for output in (self.repository / "rendered", self.root):
            with self.subTest(output=output):
                self.args.output_directory = output
                with self.assertRaises((ValueError, FileExistsError)):
                    deployment.apply_deployment(self.args)
        self.build.assert_not_called()


@unittest.skipUnless(os.name == "posix", "Bash integration runs in WSL or a container.")
class StorageSecretContextTests(unittest.TestCase):
    def test_manual_default_and_explicit_pipeline_context(self):
        source = SCRIPT.parents[2] / "serving" / "scripts" / "apply-storage-secret.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "config").mkdir()
            script = root / "scripts" / source.name
            script.write_bytes(source.read_bytes())
            config = (source.parents[1] / "config" / "storage-secret.env").read_text(encoding="utf-8")
            credentials = root / "credentials.env"
            credentials.write_text(
                "S3_ACCESS_KEY_ID=test-key\nS3_SECRET_ACCESS_KEY=test-value\nS3_REGION=test-region\n"
            )
            (root / "config" / "storage-secret.env").write_text(
                config.replace("/opt/mlops-local-poc/mlflow/.env", str(credentials))
            )
            log = root / "calls"
            stub = "#!/usr/bin/env python3\nimport json, os, sys\nwith open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(sys.argv) + '\\n')\nprint('192.0.2.1' if sys.argv[0].endswith('docker') else 'ok')\n"
            for name in ("kubectl", "docker"):
                executable = root / name
                executable.write_text(stub)
                executable.chmod(0o700)
            for context in ("", "kind-mlops-test"):
                with self.subTest(context=context):
                    log.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["bash", str(script)],
                        env={
                            **os.environ,
                            "PATH": f"{root}:{os.environ['PATH']}",
                            "COMMAND_LOG": str(log),
                            "KUBERNETES_CONTEXT": context,
                        },
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    kubectl_calls = [args for args in calls if args[0].endswith("kubectl")]
                    self.assertEqual(len(kubectl_calls), 4)
                    for args in kubectl_calls:
                        self.assertEqual(
                            args[1:3], ["--context", context] if context else ["-n", "gp-mof-capstone-serving"]
                        )
                    self.assertNotIn("test-value", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
