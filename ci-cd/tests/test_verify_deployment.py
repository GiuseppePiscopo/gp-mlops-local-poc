import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-deployment.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("verify_deployment", SCRIPT)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


class VerifyDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = argparse.Namespace(
            repository=SCRIPT.parents[2],
            deployment_result=self.root / "deployment-result.json",
            registered_model="model",
            model_version="2",
            model_uri="models:/m-target",
            run_id="target-run",
            expected_commit="a" * 40,
            context="kind-mlops-test",
            network="network",
            evaluation_mode="relative",
            event_name="push",
            git_ref="refs/heads/main",
            timeout=300,
            summary=self.root / "summary",
        )
        self.result = {
            "status": "APPLIED",
            "registered_model": "model",
            "model_version": "2",
            "model_uri": self.args.model_uri,
            "run_id": self.args.run_id,
            "git_commit": self.args.expected_commit,
            "context": self.args.context,
            "namespace": "serving",
            "images": {"serving": {"image": "runtime:git-approved"}},
            "resolution_lock": str(self.root / "model.resolution.json"),
            "kind_image": {"platform_manifest_digest": "sha256:platform", "node_image_ids": {"node": "sha256:config"}},
        }
        self.storage = "s3://artifacts/target"
        Path(self.result["resolution_lock"]).write_text(
            json.dumps({"resolution": {"resolved_storage_uri": self.storage}})
        )
        self.args.deployment_result.write_text(json.dumps(self.result))
        annotations = {
            f"mlops.gp-mof.dev/{key}": value
            for key, value in {
                "registered-model": "model",
                "resolved-version": "2",
                "source-run-id": self.args.run_id,
                "model-version-source-uri": self.args.model_uri,
                "resolved-storage-uri": self.storage,
            }.items()
        }
        self.service = {
            "metadata": {"uid": "service-uid", "annotations": annotations},
            "spec": {"predictor": {"model": {"runtime": "runtime", "storageUri": self.storage}}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        template = {
            "metadata": {"annotations": copy.deepcopy(annotations)},
            "spec": {
                "containers": [
                    {
                        "name": "kserve-container",
                        "image": "runtime:git-approved",
                        "env": [{"name": "MODEL_NAME", "value": "model"}, {"name": "MODEL_VERSION", "value": "2"}],
                    }
                ]
            },
        }
        self.deployment = {
            "metadata": {
                "uid": "deployment-uid",
                "generation": 5,
                "ownerReferences": self.owner("InferenceService", "service-uid"),
            },
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "predictor"}}, "template": template},
            "status": {
                "observedGeneration": 5,
                "replicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
            },
        }
        self.pod = copy.deepcopy(template)
        self.pod["metadata"].update(
            name="new-pod", uid="pod-uid", ownerReferences=self.owner("ReplicaSet", "rs-uid", "new-rs")
        )
        self.pod["spec"].update(
            nodeName="node", initContainers=[{"name": "storage-initializer", "args": [self.storage, "/mnt/models"]}]
        )
        self.pod["status"] = {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "kserve-container",
                    "ready": True,
                    "state": {"running": {}},
                    "imageID": "import@sha256:manifest",
                    "containerID": "containerd://container",
                }
            ],
        }
        self.replica_set = {
            "metadata": {"uid": "rs-uid", "ownerReferences": self.owner("Deployment", "deployment-uid")}
        }
        self.reply = {"model_name": "model", "outputs": [{"shape": [2, 1], "data": [1.5, 2.5]}]}
        self.failure = None
        self.changed_after_inference = False
        self.inferred = False
        self.calls = []
        self.enterContext(patch.object(verification, "run_command", side_effect=self.fake_command))
        self.promote = self.enterContext(
            patch.object(
                verification,
                "promote_verified_model",
                return_value={"status": "PROMOTED", "model_version": "2", "previous_champion": "1"},
            )
        )

    @staticmethod
    def owner(kind, uid, name="owner"):
        return [{"kind": kind, "uid": uid, "name": name, "controller": True}]

    def fake_command(self, arguments, **kwargs):
        self.calls.append(arguments)
        if self.failure is not None and self.failure(arguments):
            raise subprocess.CalledProcessError(1, arguments, stderr="Injected failure")
        if arguments[0] == "git":
            return self.args.expected_commit
        if arguments[0] == sys.executable:
            return subprocess.run(arguments, text=True, capture_output=True, check=True).stdout
        if arguments[:2] == ["docker", "exec"]:
            self.assertIn("ctr", arguments)
            contents = {
                "sha256:manifest": {"manifests": [{"digest": "sha256:index"}]},
                "sha256:index": {
                    "manifests": [{"digest": "sha256:platform", "platform": {"os": "linux", "architecture": "amd64"}}]
                },
                "sha256:platform": {"config": {"digest": "sha256:config"}},
            }
            return json.dumps(contents[arguments[-1]])
        self.assertEqual(arguments[:5], ["kubectl", "--context", self.args.context, "--request-timeout=10s", "-n"])
        if arguments[6] == "get":
            resource = arguments[7]
            if resource == "pods":
                pod = copy.deepcopy(self.pod)
                if self.inferred and self.changed_after_inference:
                    pod["status"]["containerStatuses"][0]["containerID"] = "containerd://restarted"
                return json.dumps({"items": [pod]})
            documents = {
                "inferenceservice": self.service,
                "deployment": self.deployment,
                "servingruntime": {"spec": {"containers": [{"image": "runtime:git-approved"}]}},
                "replicaset": self.replica_set,
            }
            return json.dumps(documents[resource])
        self.assertIn("new-pod", arguments)
        if arguments[-2:] == ["cat", "/mnt/models/serving_input_example.json"]:
            return json.dumps({"dataframe_split": {"columns": ["feature"], "data": [[1], [2]]}})
        route = arguments[-1]
        if route.endswith("/infer"):
            request = json.loads(kwargs["input_text"])
            self.assertEqual(
                request["inputs"], [{"name": "feature", "datatype": "FP64", "shape": [2, 1], "data": [[1], [2]]}]
            )
            self.inferred = True
            return json.dumps({"status": 200, "body": self.reply})
        if route.endswith("/ready"):
            return json.dumps({"status": 200, "body": None})
        return json.dumps(
            {"status": 200, "body": {"name": "model", "inputs": [{"name": "feature", "datatype": "FP64"}]}}
        )

    def assert_not_promoted(self):
        self.promote.assert_not_called()
        self.assertFalse((self.root / "release-result.json").exists())
        self.assertFalse(self.args.summary.exists())

    def test_exact_ready_pod_is_smoke_tested_before_promotion(self):
        result = verification.verify_deployment(self.args)
        self.assertEqual(result["status"], "DEPLOYED")
        self.assertEqual(result["pod"]["uid"], "pod-uid")
        self.assertEqual(result["inference"], {"rows": 2, "predictions": [1.5, 2.5]})
        self.promote.assert_called_once_with(self.args, self.root / "verification-result.json")
        self.assertEqual(json.loads((self.root / "release-result.json").read_text()), result)

    def test_old_ready_service_does_not_accept_old_workload_or_incomplete_rollout(self):
        for scenario in (
            "old_template",
            "unobserved_generation",
            "old_replicas",
            "old_pod",
            "wrong_storage",
            "wrong_owner",
        ):
            with self.subTest(scenario=scenario):
                deployment, pod, replica_set = copy.deepcopy((self.deployment, self.pod, self.replica_set))
                if scenario == "old_template":
                    self.deployment["spec"]["template"]["spec"]["containers"][0]["env"][1]["value"] = "1"
                elif scenario == "unobserved_generation":
                    self.deployment["status"]["observedGeneration"] = 4
                elif scenario == "old_replicas":
                    self.deployment["status"]["replicas"] = 2
                elif scenario == "old_pod":
                    self.pod["metadata"]["annotations"]["mlops.gp-mof.dev/resolved-version"] = "1"
                elif scenario == "wrong_storage":
                    self.pod["spec"]["initContainers"][0]["args"][0] = "s3://old/model"
                else:
                    self.replica_set["metadata"]["ownerReferences"][0]["uid"] = "old-deployment"
                with (
                    patch.object(verification.time, "monotonic", side_effect=[0, 301]),
                    self.assertRaises(TimeoutError),
                ):
                    verification.verify_deployment(self.args)
                self.assert_not_promoted()
                self.deployment, self.pod, self.replica_set = deployment, pod, replica_set

    def test_pending_reconciliation_is_retried_until_new_workload_is_ready(self):
        inspect = verification.inspect_rollout
        with (
            patch.object(
                verification,
                "inspect_rollout",
                side_effect=[
                    (None, "Waiting for new Deployment"),
                    inspect({**self.result, "storage_uri": self.storage}),
                ],
            ),
            patch.object(verification.time, "sleep") as sleep,
        ):
            pod = verification.wait_for_predictor({**self.result, "storage_uri": self.storage}, 300)
        self.assertEqual(pod["uid"], "pod-uid")
        sleep.assert_called_once()

    def test_wrong_running_image_is_rejected_even_with_expected_image_tag(self):
        self.result["kind_image"]["node_image_ids"]["node"] = "sha256:wrong-config"
        self.args.deployment_result.write_text(json.dumps(self.result))
        with self.assertRaisesRegex(ValueError, "Running predictor image differs"):
            verification.verify_deployment(self.args)
        self.assert_not_promoted()

    def test_bad_inference_or_pod_restart_prevents_promotion(self):
        replies = [
            {"model_name": "old-model", "outputs": [{"shape": [2], "data": [1, 2]}]},
            {"model_name": "model", "outputs": [{"shape": [1], "data": [1]}]},
            {"model_name": "model", "outputs": [{"shape": [1, 2], "data": [1, 2]}]},
            {"model_name": "model", "outputs": [{"shape": [2], "data": [1, float("nan")]}]},
            {"model_name": "model", "outputs": [{"shape": [2], "data": [1, "2"]}]},
        ]
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                self.reply = reply
                verification.verify_deployment(self.args)
            self.assert_not_promoted()
        self.reply = {"model_name": "model", "outputs": [{"shape": [2], "data": [1, 2]}]}
        self.inferred = False
        self.changed_after_inference = True
        with self.assertRaisesRegex(ValueError, "changed during inference"):
            verification.verify_deployment(self.args)
        self.assert_not_promoted()

    def test_kubernetes_or_http_failures_do_not_promote(self):
        for stage in ("get", "/infer"):
            self.failure = lambda arguments, stage=stage: stage in arguments or arguments[-1].endswith(stage)
            with self.subTest(stage=stage), self.assertRaises(subprocess.CalledProcessError):
                verification.verify_deployment(self.args)
            self.assert_not_promoted()

    def test_mismatched_application_receipt_stops_before_readiness(self):
        self.result["model_version"] = "1"
        self.args.deployment_result.write_text(json.dumps(self.result))
        with self.assertRaisesRegex(ValueError, "Application receipt differs"):
            verification.verify_deployment(self.args)
        self.assertEqual(self.calls, [])
        self.assert_not_promoted()

    def test_promotion_error_does_not_publish_success(self):
        self.promote.side_effect = subprocess.CalledProcessError(1, ["docker"], stderr="Registry unavailable")
        with self.assertRaises(subprocess.CalledProcessError):
            verification.verify_deployment(self.args)
        self.assertTrue((self.root / "verification-result.json").exists())
        self.assertFalse((self.root / "release-result.json").exists())
        self.assertFalse(self.args.summary.exists())


class PredictorHttpTests(unittest.TestCase):
    def test_pod_http_client_sends_inference_payload_and_propagates_http_errors(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                if not self.path.endswith("/ready"):
                    self.wfile.write(b'{"name":"model","inputs":[]}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(
                    (
                        self.headers["Content-Type"],
                        json.loads(body) if body else None,
                    )
                )
                if self.path == "/fail/infer":
                    self.send_error(503)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"model_name":"model","outputs":[]}')

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            code = verification.POD_HTTP_CLIENT.replace(
                "http://127.0.0.1:8080", f"http://127.0.0.1:{server.server_port}"
            )
            with patch.object(verification, "POD_HTTP_CLIENT", code):
                verification.check_model_ready([], "/model")
                self.assertEqual(verification.get_model_metadata([], "/model"), {"name": "model", "inputs": []})
                reply = verification.infer_model([], "/model", '{"inputs":[]}')
                self.assertEqual(reply["model_name"], "model")
                self.assertEqual(requests, [("application/json", {"inputs": []})])
                verification.infer_model([], "/model", "")
                self.assertEqual(requests[-1], ("application/json", None))
                with self.assertRaises(subprocess.CalledProcessError) as error:
                    verification.infer_model([], "/fail", '{"inputs":[]}')
                self.assertIn("503", error.exception.stderr)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
