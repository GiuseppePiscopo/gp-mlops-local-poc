"""Register the evaluated logged model and assign candidate only after READY."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from common.test_set_persistence import snapshot_checksum

import mlflow


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-result", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--input-snapshot", type=Path, required=True)
    parser.add_argument("--registered-model", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--git-ref", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser.parse_args()


def register_candidate(args: argparse.Namespace) -> dict:
    # Refuse PRs and non-main pushes before contacting the Registry.
    if args.event_name != "workflow_dispatch" and not (args.event_name == "push" and args.git_ref == "refs/heads/main"):
        raise ValueError("Registration requires a push to main or a manual workflow run.")
    if args.result.exists():
        raise ValueError(f"Registration result already exists: {args.result}")
    training = json.loads(args.training_result.read_text(encoding="utf-8"))
    report = json.loads(args.evaluation.read_text(encoding="utf-8"))
    snapshot = json.loads(args.input_snapshot.read_text(encoding="utf-8"))
    candidate = {key: training[key] for key in ("run_id", "model_uri", "git_commit")}
    manifest = training["test_set_manifest"]

    # Require the exact successful gate, dataset and target model from this execution.
    if (
        not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit)
        or candidate["git_commit"] != args.expected_commit
        or snapshot["git_commit"] != args.expected_commit
        or snapshot["registered_model"] != args.registered_model
        or report["candidate"] != candidate
        or report["passed"] is not True
        or report["mode"] != snapshot["mode"]
        or report["champion"] != snapshot["champion"]
        or manifest["candidate"] != candidate
        or manifest["input_snapshot_sha256"] != snapshot_checksum(snapshot)
        or report["test_set"] != manifest["test_set"]
        or report["test_set"]["dataset_sha256"] != snapshot["dataset"]["sha256"]
    ):
        raise ValueError("Registration inputs do not match a successful evaluation of this candidate.")
    if report["mode"] not in ("relative", "bootstrap"):
        raise ValueError("Unknown evaluation mode.")
    if report["mode"] == "bootstrap" and args.event_name != "workflow_dispatch":
        raise ValueError("Initial model registration requires a manual workflow run.")

    # Verify the completed source run and its successful evaluation provenance.
    client = mlflow.MlflowClient()
    run = client.get_run(candidate["run_id"])
    if (
        run.info.status != "FINISHED"
        or run.data.tags.get("git.commit") != args.expected_commit
        or run.data.tags.get("dataset.sha256") != snapshot["dataset"]["sha256"]
        or run.data.tags.get("evaluation.passed") != "true"
    ):
        raise ValueError("Source run is unfinished or does not match the successful evaluation provenance.")

    # Compare local handoff files with the evidence persisted in the exact source run.
    with tempfile.TemporaryDirectory(prefix="mlops-registration-evidence-") as directory:
        for artifact, expected in (
            ("evaluation/report.json", report),
            ("evaluation/input-snapshot.json", snapshot),
            ("evaluation/test-set-manifest.json", manifest),
        ):
            downloaded = client.download_artifacts(candidate["run_id"], artifact, directory)
            if json.loads(Path(downloaded).read_text(encoding="utf-8")) != expected:
                raise ValueError(f"Registration input differs from the source run artifact: {artifact}")

    # Bind the logged-model URI to the evaluated run without resolving aliases.
    if not re.fullmatch(r"models:/m-[0-9a-f]{32}", candidate["model_uri"]):
        raise ValueError("Expected the exact logged-model URI returned by training.")
    logged_model = client.get_logged_model(candidate["model_uri"].removeprefix("models:/"))
    if logged_model.source_run_id != candidate["run_id"]:
        raise ValueError("Logged model does not belong to the evaluated source run.")

    # Register without training again, then verify the concrete version before changing any alias.
    created = mlflow.register_model(
        candidate["model_uri"],
        args.registered_model,
        await_registration_for=120,
        tags={"git.commit": args.expected_commit, "evaluation.mode": report["mode"]},
    )
    version = str(created.version)
    print(f"Created Model Version {args.registered_model}/{version}; candidate alias not yet updated.", flush=True)
    verified = client.get_model_version(args.registered_model, version)
    if (
        verified.status != "READY"
        or verified.run_id != candidate["run_id"]
        or verified.source != candidate["model_uri"]
    ):
        raise ValueError(
            f"Created Model Version {args.registered_model}/{version} is not READY with the expected source."
        )

    # Assign candidate only to the verified READY version and confirm the update.
    client.set_registered_model_alias(args.registered_model, "candidate", version)
    if str(client.get_model_version_by_alias(args.registered_model, "candidate").version) != version:
        raise ValueError(
            f"Created Model Version {args.registered_model}/{version}, but candidate alias verification failed."
        )

    # Publish a receipt only after readiness and alias assignment have both succeeded.
    result = {
        **candidate,
        "registered_model": args.registered_model,
        "model_version": version,
        "status": "READY",
    }
    with args.result.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main() -> None:
    args = parse_arguments()
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    register_candidate(args)


if __name__ == "__main__":
    main()
