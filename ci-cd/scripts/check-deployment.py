#!/usr/bin/env python3
"""Verify the approved version and reject an obsolete evaluation before deployment."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from common.test_set_persistence import snapshot_checksum
from common.training_snapshot import champion_exists, snapshot_champion

import mlflow


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-uri", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--registered-model", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--evaluation-mode", choices=("relative", "bootstrap"), required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--git-ref", required=True)
    return parser.parse_args()


def check_deployment(args: argparse.Namespace) -> dict:
    # Bind the deployment request to an eligible main/manual workflow and exact identifiers.
    if args.git_ref != "refs/heads/main" or args.event_name not in ("push", "workflow_dispatch"):
        raise ValueError("Deployment requires a push to main or a manual run on main.")
    if args.evaluation_mode == "bootstrap" and args.event_name != "workflow_dispatch":
        raise ValueError("Initial model deployment requires a manual workflow run.")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", args.run_id)
        or not re.fullmatch(r"models:/m-[0-9a-f]{32}", args.model_uri)
        or not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit)
        or not re.fullmatch(r"[1-9][0-9]*", args.model_version)
        or args.evaluation_mode not in ("relative", "bootstrap")
        or not args.registered_model.strip()
    ):
        raise ValueError("Invalid deployment identifiers or evaluation mode.")
    candidate = {"run_id": args.run_id, "model_uri": args.model_uri, "git_commit": args.expected_commit}
    client = mlflow.MlflowClient()

    # Verify the exact registered version, never resolving candidate to select the target.
    version = client.get_model_version(args.registered_model, args.model_version)
    if (
        version.name != args.registered_model
        or str(version.version) != args.model_version
        or version.status != "READY"
        or version.run_id != args.run_id
        or version.source != args.model_uri
        or version.tags.get("git.commit") != args.expected_commit
        or version.tags.get("evaluation.mode") != args.evaluation_mode
    ):
        raise ValueError("Approved Model Version is not READY with the expected run, source and provenance.")

    # Require the completed, successfully evaluated source run.
    run = client.get_run(args.run_id)
    if (
        run.info.status != "FINISHED"
        or run.data.tags.get("git.commit") != args.expected_commit
        or run.data.tags.get("evaluation.passed") != "true"
    ):
        raise ValueError("Source run does not identify a finished, successfully evaluated candidate.")

    # Bind the logged model to that source run.
    if client.get_logged_model(args.model_uri.removeprefix("models:/")).source_run_id != args.run_id:
        raise ValueError("Logged model does not belong to the approved source run.")

    # Retrieve the immutable reference from the exact evaluated run, not the runner workspace.
    with tempfile.TemporaryDirectory(prefix="mlops-deployment-evidence-") as directory:
        evidence = {}
        for name in ("report", "input-snapshot", "test-set-manifest"):
            downloaded = client.download_artifacts(args.run_id, f"evaluation/{name}.json", directory)
            evidence[name] = json.loads(Path(downloaded).read_text(encoding="utf-8"))

    # Cross-check the report, snapshot and test-set manifest against the approved target.
    report, snapshot, manifest = (evidence[name] for name in ("report", "input-snapshot", "test-set-manifest"))
    if (
        report["passed"] is not True
        or report["candidate"] != candidate
        or report["mode"] != args.evaluation_mode
        or snapshot["mode"] != args.evaluation_mode
        or snapshot["git_commit"] != args.expected_commit
        or snapshot["registered_model"] != args.registered_model
        or report["champion"] != snapshot["champion"]
        or manifest["candidate"] != candidate
        or manifest["input_snapshot_sha256"] != snapshot_checksum(snapshot)
        or report["test_set"] != manifest["test_set"]
        or report["test_set"]["dataset_sha256"] != snapshot["dataset"]["sha256"]
        or run.data.tags.get("dataset.sha256") != snapshot["dataset"]["sha256"]
    ):
        raise ValueError("Deployment evidence does not match the approved candidate and successful evaluation.")
    expected = snapshot["champion"]
    if (expected is None) != (args.evaluation_mode == "bootstrap"):
        raise ValueError("Champion reference is inconsistent with the evaluation mode.")

    # Under the deployment concurrency group, reject a reference changed during approval.
    current = (
        snapshot_champion(client, args.registered_model) if champion_exists(client, args.registered_model) else None
    )
    if current != expected:
        next_run = "an ordinary" if args.evaluation_mode == "bootstrap" else "a new"
        raise ValueError(
            f"Obsolete deployment: evaluated champion={json.dumps(expected, sort_keys=True)}, "
            f"current champion={json.dumps(current, sort_keys=True)}. "
            f"Start {next_run} workflow execution with a new evaluation and approval."
        )
    return {
        **candidate,
        "registered_model": args.registered_model,
        "model_version": args.model_version,
        "evaluation_mode": args.evaluation_mode,
        "champion": current,
    }


def main() -> None:
    args = parse_arguments()
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    print(json.dumps(check_deployment(args), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
