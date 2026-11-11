#!/usr/bin/env python3
"""Render and apply the approved version, without readiness checks or alias promotion."""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from prepare_image import prepare_image


@dataclass(frozen=True)
class RenderedDeployment:
    resolution: dict
    manifest: Path
    lock: Path
    runtime: Path


def command(arguments: list[str], *, env: dict[str, str] | None = None) -> str:
    return subprocess.run(arguments, text=True, capture_output=True, check=True, env=env).stdout.strip()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--registered-model", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--model-uri", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def render_manifests(args: argparse.Namespace, images: dict) -> RenderedDeployment:
    repository = args.repository.resolve()
    output = args.output_directory.resolve()

    # Render the exact Model Version with the verified resolver image.
    resolved = json.loads(
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                args.network,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                f"type=bind,src={output},dst=/output",
                images["deployment"]["image_id"],
                "--registered-model",
                args.registered_model,
                "--version",
                args.model_version,
            ]
        )
    )

    # Require the rendered resolution to match the approved model and source run.
    resolution = resolved["resolution"]
    expected = {
        "registered_model": args.registered_model,
        "requested_alias": None,
        "requested_version": args.model_version,
        "resolved_version": args.model_version,
        "source_run_id": args.run_id,
        "model_version_source_uri": args.model_uri,
        "status": "READY",
    }
    if any(resolution[key] != value for key, value in expected.items()):
        raise ValueError("Rendered resolution differs from the approved version and provenance.")

    # Match the generated paths and persisted lock to that same resolution.
    filename = f"{args.registered_model}-v{args.model_version}.yaml"
    manifest = output / filename
    lock = manifest.with_suffix(".resolution.json")
    if (
        resolved["rendered_manifest_path"] != f"/output/{filename}"
        or resolved["resolution_lock_path"] != f"/output/{lock.name}"
        or json.loads(lock.read_text(encoding="utf-8"))
        != {
            "rendered_manifest_filename": filename,
            "resolution": resolution,
        }
    ):
        raise ValueError("Resolution lock does not match the rendered deployment.")

    # Render the runtime using the verified serving image and committed template.
    runtime = output / "20-serving-runtime.yaml"
    runtime_identity = json.loads(
        command(
            [
                sys.executable,
                str(repository / "serving" / "scripts" / "render-runtime.py"),
                "--git-repository",
                str(repository),
                "--image-tag",
                images["serving"]["image"],
                "--output",
                str(runtime),
            ]
        )
    )
    if any(runtime_identity[key] != images["serving"][key] for key in ("image", "image_id")):
        raise ValueError("Rendered runtime does not reference the verified serving image.")
    return RenderedDeployment(resolution, manifest, lock, runtime)


def validate_manifests(args: argparse.Namespace, rendered: RenderedDeployment, serving_image: dict) -> str:
    # Read the manifests through kubectl, without adding a host-side YAML dependency.
    repository = args.repository.resolve()
    resolution = rendered.resolution
    kubectl = ["kubectl", "--context", args.context]
    namespace_file = repository / "serving" / "manifests" / "00-namespace.yaml"
    account_file = repository / "serving" / "manifests" / "10-service-account.yaml"
    documents = [
        json.loads(command([*kubectl, "create", "--dry-run=client", "-f", str(path), "-o", "json"]))
        for path in (namespace_file, account_file, rendered.runtime, rendered.manifest)
    ]
    namespace, account, runtime_document, service = documents
    namespace_name = namespace["metadata"]["name"]
    model = service["spec"]["predictor"]["model"]

    # Keep all serving resources in the intended namespace with matching references.
    if (
        namespace["kind"] != "Namespace"
        or account["kind"] != "ServiceAccount"
        or runtime_document["kind"] != "ServingRuntime"
        or service["kind"] != "InferenceService"
        or any(document["metadata"]["namespace"] != namespace_name for document in documents[1:])
        or service["metadata"]["name"] != args.registered_model
        or service["spec"]["predictor"]["serviceAccountName"] != account["metadata"]["name"]
        or model["runtime"] != runtime_document["metadata"]["name"]
    ):
        raise ValueError("Rendered serving resources do not match the approved model, runtime and namespace.")

    # Bind model storage and provenance annotations to the verified resolution.
    if (
        model["storageUri"] != resolution["resolved_storage_uri"]
        or not model["storageUri"].startswith("s3://")
        or any(
            service["metadata"]["annotations"].get(f"mlops.gp-mof.dev/{key.replace('_', '-')}") != resolution[key]
            for key in (
                "registered_model",
                "requested_version",
                "resolved_version",
                "source_run_id",
                "model_version_source_uri",
                "model_version_download_uri",
                "resolved_storage_uri",
            )
        )
    ):
        raise ValueError("Rendered model storage or provenance differs from the approved resolution.")

    # Set logging identity per model, without changing the shared runtime defaults.
    if {item["name"]: item.get("value") for item in model["env"]} != {
        "MODEL_NAME": args.registered_model,
        "MODEL_VERSION": args.model_version,
    } or runtime_document["spec"]["containers"][0]["image"] != serving_image["image"]:
        raise ValueError("Rendered logging identity or runtime image differs from the approved target.")

    # Check the existing non-secret storage configuration before applying any resource.
    storage_config = command(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            'source "$1"; printf "%s\\n" "$KUBERNETES_NAMESPACE" "$SERVICE_ACCOUNT_NAME" "$S3_SECRET_NAME"',
            "check-storage-config",
            str(repository / "serving" / "config" / "storage-secret.env"),
        ]
    ).splitlines()
    if storage_config != [
        namespace_name,
        account["metadata"]["name"],
        service["metadata"]["annotations"]["serving.kserve.io/secretName"],
    ]:
        raise ValueError("Storage configuration does not match the rendered namespace, account and Secret.")
    return namespace_name


def prepare_cluster(args: argparse.Namespace, serving_image: dict) -> dict:
    # Verify the explicit context targets the selected KinD cluster.
    kubectl = ["kubectl", "--context", args.context]
    nodes = command(["kind", "get", "nodes", "--name", args.cluster]).splitlines()
    cluster_nodes = json.loads(command([*kubectl, "get", "nodes", "-o", "json"]))
    if not nodes or set(nodes) != {node["metadata"]["name"] for node in cluster_nodes["items"]}:
        raise ValueError("Kubernetes context does not match the selected KinD nodes.")

    # Attach each node to the artifact-storage network if necessary.
    for node in nodes:
        networks = json.loads(command(["docker", "inspect", node, "--format", "{{json .NetworkSettings.Networks}}"]))
        if args.network not in networks:
            command(["docker", "network", "connect", args.network, node])

    # Resolve the platform manifest; Docker's image ID may instead identify an OCI index.
    descriptor = json.loads(
        command(
            [
                "docker",
                "image",
                "inspect",
                "--platform",
                "linux/amd64",
                serving_image["image"],
                "--format",
                "{{json .Descriptor}}",
            ]
        )
    )

    # Load only the serving image and bind each CRI image ID to its exact platform config.
    command(["kind", "load", "docker-image", serving_image["image"], "--name", args.cluster])
    node_images = {}
    for node in nodes:
        platform_manifest = json.loads(
            command(["docker", "exec", node, "ctr", "--namespace", "k8s.io", "content", "get", descriptor["digest"]])
        )
        loaded = json.loads(command(["docker", "exec", node, "crictl", "inspecti", serving_image["image"]]))
        if loaded["status"]["id"] != platform_manifest["config"]["digest"]:
            raise ValueError(f"Serving image ID mismatch on KinD node {node}.")
        node_images[node] = loaded["status"]["id"]
    return {"platform_manifest_digest": descriptor["digest"], "node_image_ids": node_images}


def apply_resources(args: argparse.Namespace, rendered: RenderedDeployment) -> None:
    # Reuse the storage setup with an explicit context; never include credentials in evidence.
    repository = args.repository.resolve()
    kubectl = ["kubectl", "--context", args.context]
    namespace_file = repository / "serving" / "manifests" / "00-namespace.yaml"
    account_file = repository / "serving" / "manifests" / "10-service-account.yaml"
    command([*kubectl, "apply", "-f", str(namespace_file)])
    command(
        ["bash", str(repository / "serving" / "scripts" / "apply-storage-secret.sh")],
        env={**os.environ, "KUBERNETES_CONTEXT": args.context},
    )
    command([*kubectl, "apply", "-f", str(account_file)])

    # Validate both model resources on the server before applying either one.
    for path in (rendered.runtime, rendered.manifest):
        command([*kubectl, "apply", "--dry-run=server", "-f", str(path)])
    for path in (rendered.runtime, rendered.manifest):
        command([*kubectl, "apply", "-f", str(path)])


def record_application(
    args: argparse.Namespace, images: dict, rendered: RenderedDeployment, namespace: str, kind_image: dict
) -> dict:
    # Record application separately from readiness, inference and champion promotion.
    output = args.output_directory.resolve()
    result = {
        "registered_model": args.registered_model,
        "model_version": args.model_version,
        "run_id": args.run_id,
        "model_uri": args.model_uri,
        "git_commit": args.expected_commit,
        "context": args.context,
        "namespace": namespace,
        "images": images,
        "kind_image": kind_image,
        "manifest": str(rendered.manifest),
        "resolution_lock": str(rendered.lock),
        "runtime_manifest": str(rendered.runtime),
        "status": "APPLIED",
    }
    with (output / "deployment-result.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    with args.summary.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## Deployment apply\n\n"
            f"- Model: `{args.registered_model}/{args.model_version}`\n"
            f"- Context: `{args.context}`\n"
            f"- Storage URI: `{rendered.resolution['resolved_storage_uri']}`\n"
            f"- Runtime image: `{images['serving']['image']}`\n"
            f"- Runtime image ID: `{images['serving']['image_id']}`\n\n"
            "Manifests applied. Readiness, inference and champion promotion follow in the verification step.\n"
        )
    return result


def apply_deployment(args: argparse.Namespace) -> dict:
    # Keep output outside the checkout and verify the approved commit and target context.
    repository = args.repository.resolve()
    output = args.output_directory.resolve()
    if output.is_relative_to(repository):
        raise ValueError("Deployment output must be outside the Git checkout.")
    if command(["git", "-C", str(repository), "rev-parse", "HEAD"]) != args.expected_commit:
        raise ValueError("Approved model commit does not match the deployment checkout.")
    if args.context != f"kind-{args.cluster}":
        raise ValueError("Deployment context must identify the selected KinD cluster.")
    output.mkdir(mode=0o700)

    # Build or reuse both images through the shared provenance checks.
    images = {component: prepare_image("build", repository, component, None) for component in ("deployment", "serving")}
    if any(identity["git_commit"] != args.expected_commit for identity in images.values()):
        raise ValueError("Deployment images do not match the approved source commit.")

    # Validate all identities before loading the image or applying Kubernetes resources.
    rendered = render_manifests(args, images)
    namespace = validate_manifests(args, rendered, images["serving"])
    kind_image = prepare_cluster(args, images["serving"])
    apply_resources(args, rendered)
    return record_application(args, images, rendered, namespace, kind_image)


def main() -> None:
    try:
        print(json.dumps(apply_deployment(parse_arguments()), indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(f"Deployment application failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
