#!/usr/bin/env python3
"""Verify the approved predictor and inference before promoting the exact version."""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from prepare_image import prepare_image

# Sends an HTTP request to the predictor and returns its status and decoded JSON body.
# Runs inside the predictor via kubectl exec.
# Input:
#   - argv[1] = HTTP method,
#   - argv[2] = path on localhost:8080;
#   - stdin = JSON payload or empty.
# Output:
#   - stdout = JSON {"status": HTTP status, "body": parsed JSON or null};
#   - HTTP errors exit nonzero.
POD_HTTP_CLIENT = """
import json, sys
from urllib.request import Request, urlopen
data = sys.stdin.buffer.read()
request = Request("http://127.0.0.1:8080" + sys.argv[2], method=sys.argv[1],
                  data=data or None, headers={"Content-Type": "application/json"})
with urlopen(request, timeout=15) as response:
    body = response.read()
    print(json.dumps({"status": response.status, "body": json.loads(body) if body else None}))
"""


def run_command(arguments: list[str], *, input_text: str = "") -> str:
    return subprocess.run(
        arguments, input=input_text, text=True, capture_output=True, check=True, timeout=60
    ).stdout.strip()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--deployment-result", type=Path, required=True)
    parser.add_argument("--registered-model", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--model-uri", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--evaluation-mode", choices=("relative", "bootstrap"), required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--git-ref", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def read_application(args: argparse.Namespace) -> dict:
    result = json.loads(args.deployment_result.read_text(encoding="utf-8"))
    expected = {
        "status": "APPLIED",
        "registered_model": args.registered_model,
        "model_version": args.model_version,
        "model_uri": args.model_uri,
        "run_id": args.run_id,
        "git_commit": args.expected_commit,
        "context": args.context,
    }
    if any(result[key] != value for key, value in expected.items()) or args.timeout <= 0:
        raise ValueError("Application receipt differs from the approved deployment, or timeout is not positive.")

    # Keep model provenance and verification code on the same approved commit.
    if run_command(["git", "-C", str(args.repository), "rev-parse", "HEAD"]) != args.expected_commit:
        raise ValueError("Verification checkout differs from the approved commit.")
    return result


def build_kubectl_command(result: dict) -> list[str]:
    return ["kubectl", "--context", result["context"], "--request-timeout=10s", "-n", result["namespace"]]


def get_resource(result: dict, resource: str, name: str) -> dict:
    document = run_command([*build_kubectl_command(result), "get", resource, name, "--ignore-not-found", "-o", "json"])
    return json.loads(document) if document else {}


def is_controlled_by(document: dict, kind: str, uid: str) -> bool:
    return any(
        owner.get("controller") is True and owner["kind"] == kind and owner["uid"] == uid
        for owner in document["metadata"].get("ownerReferences", [])
    )


def matches_target(document: dict, result: dict) -> bool:
    annotations = document["metadata"].get("annotations", {})
    expected = {
        "registered-model": result["registered_model"],
        "resolved-version": result["model_version"],
        "source-run-id": result["run_id"],
        "model-version-source-uri": result["model_uri"],
        "resolved-storage-uri": result["storage_uri"],
    }
    return all(annotations.get(f"mlops.gp-mof.dev/{key}") == value for key, value in expected.items())


def matches_predictor(spec: dict, result: dict) -> bool:
    container = next((item for item in spec["containers"] if item["name"] == "kserve-container"), {})
    environment = {item["name"]: item.get("value") for item in container.get("env", [])}
    return (
        container.get("image") == result["images"]["serving"]["image"]
        and environment.get("MODEL_NAME") == result["registered_model"]
        and environment.get("MODEL_VERSION") == result["model_version"]
    )


def inspect_rollout(result: dict) -> tuple[dict | None, str]:
    # Reject changes to the approved service and runtime before checking readiness.
    service = get_resource(result, "inferenceservice", result["registered_model"])
    if not service or not matches_target(service, result):
        raise ValueError("Applied InferenceService is missing or its approved provenance changed.")
    model = service["spec"]["predictor"]["model"]
    if model["storageUri"] != result["storage_uri"]:
        raise ValueError("Applied model storage changed during verification.")
    runtime = get_resource(result, "servingruntime", model["runtime"])
    if not runtime or runtime["spec"]["containers"][0]["image"] != result["images"]["serving"]["image"]:
        raise ValueError("ServingRuntime no longer references the approved image.")

    # KServe Ready alone may still describe the previous model.
    if not any(
        item["type"] == "Ready" and item["status"] == "True" for item in service.get("status", {}).get("conditions", [])
    ):
        return None, "InferenceService is not Ready."

    # Require the generated Deployment to identify the approved model and runtime.
    deployment = get_resource(result, "deployment", f"{result['registered_model']}-predictor")
    if not deployment:
        return None, "Predictor Deployment has not been created."
    template = deployment["spec"]["template"]
    if (
        not is_controlled_by(deployment, "InferenceService", service["metadata"]["uid"])
        or not matches_target(template, result)
        or not matches_predictor(template["spec"], result)
    ):
        return None, "Predictor Deployment has not reconciled the approved model and runtime."

    # Wait until the controller has observed this generation and all replicas are updated and ready.
    status = deployment.get("status", {})
    replicas = deployment["spec"].get("replicas", 1)
    if (
        replicas < 1
        or status.get("observedGeneration", 0) < deployment["metadata"]["generation"]
        or any(
            status.get(key, 0) != replicas
            for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")
        )
    ):
        return None, f"Predictor rollout is incomplete: {json.dumps(status, sort_keys=True)}"

    pod = find_ready_pod(result, deployment)
    return (pod, "") if pod else (None, "No Ready pod owned by the approved predictor Deployment.")


def find_ready_pod(result: dict, deployment: dict) -> dict | None:
    # Select a Ready pod from a ReplicaSet owned by this Deployment, not an old arbitrary pod.
    selector = ",".join(f"{key}={value}" for key, value in deployment["spec"]["selector"]["matchLabels"].items())
    pods = json.loads(run_command([*build_kubectl_command(result), "get", "pods", "-l", selector, "-o", "json"]))[
        "items"
    ]
    for pod in pods:
        # Skip terminating pods and pods still carrying an earlier target.
        if (
            pod["metadata"].get("deletionTimestamp")
            or not matches_target(pod, result)
            or not matches_predictor(pod["spec"], result)
        ):
            continue

        # Require the storage initializer to load the approved artifact URI.
        initializer = next(
            (item for item in pod["spec"].get("initContainers", []) if item["name"] == "storage-initializer"), {}
        )
        if initializer.get("args") != [result["storage_uri"], "/mnt/models"]:
            continue

        # Follow controller ownership instead of trusting labels alone.
        owners = [
            item
            for item in pod["metadata"].get("ownerReferences", [])
            if item.get("controller") and item["kind"] == "ReplicaSet"
        ]
        if len(owners) != 1:
            continue
        replica_set = get_resource(result, "replicaset", owners[0]["name"])
        if (
            not replica_set
            or replica_set["metadata"]["uid"] != owners[0]["uid"]
            or not is_controlled_by(replica_set, "Deployment", deployment["metadata"]["uid"])
        ):
            continue

        # Require both pod-level and predictor-container readiness.
        pod_status = pod.get("status", {})
        container = next(
            (item for item in pod_status.get("containerStatuses", []) if item["name"] == "kserve-container"), {}
        )
        if (
            pod_status.get("phase") != "Running"
            or container.get("ready") is not True
            or "running" not in container.get("state", {})
            or not any(
                item["type"] == "Ready" and item["status"] == "True" for item in pod_status.get("conditions", [])
            )
        ):
            continue

        # Resolve the running pod's image reference, which can differ from Docker's digest.
        node = pod["spec"]["nodeName"]
        verify_running_image(result, node, container["imageID"])
        return {
            "name": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "container_id": container["containerID"],
            "node": node,
            "image_id": container["imageID"],
            "deployment_generation": deployment["metadata"]["generation"],
        }
    return None


def verify_running_image(result: dict, node: str, image_id: str) -> None:
    # A CRI image ID may already identify the expected config directly.
    expected_config = result["kind_image"]["node_image_ids"][node]
    root = image_id.rsplit("@", 1)[-1]
    if root == expected_config:
        return

    # KinD can expose a synthetic import index, wrapping the original platform manifest.
    pending = [root]
    visited = set()
    while pending:
        digest = pending.pop()
        if digest in visited:
            continue
        visited.add(digest)
        document = json.loads(
            run_command(["docker", "exec", node, "ctr", "--namespace", "k8s.io", "content", "get", digest])
        )

        # Accept only the approved platform manifest and its verified config.
        if digest == result["kind_image"]["platform_manifest_digest"]:
            if document["config"]["digest"] == expected_config:
                return
            break

        # Ignore attestations and manifests for other platforms.
        for manifest in document.get("manifests", []):
            platform = manifest.get("platform")
            if platform is None or (platform.get("os") == "linux" and platform.get("architecture") == "amd64"):
                pending.append(manifest["digest"])
    raise ValueError("Running predictor image differs from the image verified during KinD load.")


def wait_for_predictor(result: dict, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        pod, reason = inspect_rollout(result)
        if pod is not None:
            return pod
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Predictor readiness timed out after {timeout}s. {reason}")
        time.sleep(min(3, max(0, deadline - time.monotonic())))


def validate_inference(response: dict, model: str, rows: int) -> list:
    if response["model_name"] != model or rows < 1 or len(response["outputs"]) != 1:
        raise ValueError("Inference response does not identify the expected regression model and output.")

    # Require one scalar prediction per input row, allowing singleton dimensions.
    output = response["outputs"][0]
    shape = output["shape"]
    if (
        not shape
        or any(type(size) is not int or size < 1 for size in shape)
        or shape[0] != rows
        or math.prod(shape) != rows
    ):
        raise ValueError("Inference output shape does not match the input row count.")

    # Normalize flat and nested tensor data before checking finite numeric values.
    values = output["data"]
    while any(isinstance(value, list) for value in values):
        values = [item for value in values for item in (value if isinstance(value, list) else [value])]
    if len(values) != rows or any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Inference must return one finite numeric prediction per input row.")
    return values


def send_model_request(execute: list[str], route: str, *, method: str, payload: str = "") -> dict:
    reply = json.loads(run_command([*execute, "python", "-c", POD_HTTP_CLIENT, method, route], input_text=payload))
    if reply["status"] != 200:
        raise ValueError(f"Model HTTP check failed for {route}.")
    return reply


def check_model_ready(execute: list[str], route: str) -> None:
    send_model_request(execute, route + "/ready", method="GET")


def get_model_metadata(execute: list[str], route: str) -> dict:
    return send_model_request(execute, route, method="GET")["body"]


def infer_model(execute: list[str], route: str, payload: str) -> dict:
    return send_model_request(execute, route + "/infer", method="POST", payload=payload)["body"]


def run_smoke_test(args: argparse.Namespace, result: dict, pod: dict) -> dict:
    # Execute HTTP requests inside the verified predictor; no persistent port-forward is needed.
    output = args.deployment_result.parent
    execute = [*build_kubectl_command(result), "exec", "-i", pod["name"], "-c", "kserve-container", "--"]
    route = f"/v2/models/{quote(result['registered_model'], safe='')}"
    check_model_ready(execute, route)
    metadata = get_model_metadata(execute, route)
    if metadata["name"] != result["registered_model"]:
        raise ValueError("Model metadata does not identify the approved model.")

    # Build the V2 payload from the example stored with this deployed model.
    example = json.loads(run_command([*execute, "cat", "/mnt/models/serving_input_example.json"]))
    for name, value in (("model-metadata", metadata), ("serving-input-example", example)):
        (output / f"{name}.json").write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    run_command(
        [
            sys.executable,
            str(args.repository / "serving" / "scripts" / "build-infer-request.py"),
            "--metadata",
            str(output / "model-metadata.json"),
            "--example",
            str(output / "serving-input-example.json"),
            "--output",
            str(output / "infer-request.json"),
        ]
    )

    # Validate the live response before saving successful inference evidence.
    response = infer_model(execute, route, (output / "infer-request.json").read_text(encoding="utf-8"))
    rows = len(example["dataframe_split"]["data"])
    predictions = validate_inference(response, result["registered_model"], rows)
    (output / "infer-response.json").write_text(json.dumps(response, allow_nan=False), encoding="utf-8")
    return {"rows": rows, "predictions": predictions}


def promote_verified_model(args: argparse.Namespace, verification: Path) -> dict:
    # Reuse the training image for MLflow; the runner host needs only the Python standard library.
    image = prepare_image("resolve", args.repository, "training", None)
    if image["git_commit"] != args.expected_commit:
        raise ValueError("Promotion image differs from the approved commit.")

    # Run Registry operations with read-only mounts of the code and verification evidence.
    arguments = [
        "docker",
        "run",
        "--rm",
        "--network",
        args.network,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        f"MLFLOW_TRACKING_URI={os.environ['MLFLOW_TRACKING_URI']}",
        "--mount",
        f"type=bind,src={args.repository / 'ci-cd' / 'scripts'},dst=/deployment,readonly",
        "--mount",
        f"type=bind,src={verification},dst=/verification.json,readonly",
        "--entrypoint",
        "python",
        image["image_id"],
        "/deployment/promote-champion.py",
        "--verification",
        "/verification.json",
    ]
    for name in (
        "registered_model",
        "model_version",
        "model_uri",
        "run_id",
        "expected_commit",
        "evaluation_mode",
        "event_name",
        "git_ref",
    ):
        arguments.extend([f"--{name.replace('_', '-')}", getattr(args, name)])
    return json.loads(run_command(arguments))


def verify_deployment(args: argparse.Namespace) -> dict:
    # Bind readiness and inference checks to the exact applied resolution.
    result = read_application(args)
    lock = json.loads(Path(result["resolution_lock"]).read_text(encoding="utf-8"))
    result["storage_uri"] = lock["resolution"]["resolved_storage_uri"]
    pod = wait_for_predictor(result, args.timeout)
    inference = run_smoke_test(args, result, pod)

    # A restart or another rollout during the smoke test invalidates this verification.
    current, reason = inspect_rollout(result)
    if current != pod:
        raise ValueError(f"Predictor changed during inference verification. {reason}")

    # Persist smoke-test evidence before starting the Registry mutation.
    verification = {
        **{
            key: result[key]
            for key in (
                "registered_model",
                "model_version",
                "model_uri",
                "run_id",
                "git_commit",
                "context",
                "namespace",
            )
        },
        "status": "VERIFIED",
        "pod": pod,
        "inference": inference,
    }
    proof = args.deployment_result.parent / "verification-result.json"
    with proof.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(verification, indent=2, allow_nan=False) + "\n")

    # Publish release success only after champion promotion is confirmed.
    promotion = promote_verified_model(args, proof)
    if promotion["status"] != "PROMOTED" or promotion["model_version"] != args.model_version:
        raise ValueError("Champion promotion did not confirm the verified version.")

    release = {**verification, "status": "DEPLOYED", "promotion": promotion}
    with (proof.parent / "release-result.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(release, indent=2, allow_nan=False) + "\n")
    with args.summary.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## Deployment verification\n\n"
            f"- Model: `{args.registered_model}/{args.model_version}`\n"
            f"- Ready predictor pod: `{pod['name']}`\n"
            f"- Running image ID: `{pod['image_id']}`\n"
            f"- Inference: `{inference['rows']}` rows, one finite numeric prediction per row.\n"
            f"- Predictions: `{inference['predictions']}`\n"
            f"- Champion: `{args.model_version}` (previous: `{promotion['previous_champion'] or 'absent'}`).\n\n"
            "Readiness, inference and champion promotion completed.\n"
        )
    return release


def run_cli() -> None:
    try:
        print(json.dumps(verify_deployment(parse_arguments()), indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TimeoutError, subprocess.SubprocessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(f"Deployment verification failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    run_cli()
