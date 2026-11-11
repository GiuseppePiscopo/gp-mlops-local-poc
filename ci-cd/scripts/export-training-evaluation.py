#!/usr/bin/env python3
"""Export verified training/evaluation metadata as workflow artifacts and outputs."""

import argparse
import json
import math
import re
import sys
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser.parse_args()


def export_results(args: argparse.Namespace) -> None:
    # Bind training and evaluation to the candidate from this checkout.
    result = json.loads(args.result.read_text(encoding="utf-8"))
    report = json.loads(args.evaluation.read_text(encoding="utf-8"))
    candidate = {key: result[key] for key in ("run_id", "model_uri", "git_commit")}
    if (
        not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit)
        or candidate["git_commit"] != args.expected_commit
        or not re.fullmatch(r"[0-9a-f]{32}", candidate["run_id"])
        or not re.fullmatch(r"models:/m-[0-9a-f]{32}", candidate["model_uri"])
        or report["candidate"] != candidate
    ):
        raise ValueError("Training/evaluation identifiers do not match the workflow candidate and commit.")

    # Validate execution identifiers before using them in artifact names and outputs.
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in (args.workflow_run_id, args.workflow_run_attempt)):
        raise ValueError("Workflow run ID and attempt must be positive integers.")

    # Require the evaluation to reference the test set persisted by training.
    manifest = result["test_set_manifest"]
    if manifest["candidate"] != candidate or report["test_set"] != manifest["test_set"]:
        raise ValueError("Evaluation test set does not match the training manifest.")

    # Validate the outcome and metric groups required by the explicit evaluation mode.
    if type(report["passed"]) is not bool:
        raise ValueError("Evaluation outcome must be a boolean.")
    if report["mode"] == "relative":
        if not report["champion"]:
            raise ValueError("Relative evaluation requires the champion reference.")
        metric_groups = (report["candidate_metrics"], report["champion_metrics"])
    elif report["mode"] == "bootstrap":
        if report["champion"] is not None or set(report["thresholds"]) != {"r2_min", "rmse_max"}:
            raise ValueError("Invalid bootstrap evaluation reference or thresholds.")
        metric_groups = (report["candidate_metrics"],)
    else:
        raise ValueError("Unknown evaluation mode.")
    for metrics in metric_groups:
        if set(metrics) != {"rmse", "mae", "r2", "mape"} or any(
            type(value) not in (int, float) or not math.isfinite(value) for value in metrics.values()
        ):
            raise ValueError("Missing or non-finite evaluation metrics.")

    # Keep outputs as strings while preserving the boolean outcome in execution metadata.
    artifact_name = f"training-evaluation-{args.workflow_run_id}-{args.workflow_run_attempt}"
    outputs = {
        **candidate,
        "evaluation_mode": report["mode"],
        "evaluation_passed": str(report["passed"]).lower(),
        "artifact_name": artifact_name,
    }
    execution = {
        "workflow_run_id": args.workflow_run_id,
        "workflow_run_attempt": args.workflow_run_attempt,
        **outputs,
        "evaluation_passed": report["passed"],
    }

    # Serialize metadata only; the ephemeral test-set payload is never included.
    documents = {
        "training-result.json": {**candidate, "test_set_manifest": manifest},
        "evaluation-report.json": report,
        "execution.json": execution,
    }
    serialized = {name: json.dumps(document, indent=2, allow_nan=False) + "\n" for name, document in documents.items()}

    # Write a new bundle without overwriting artifacts from an earlier invocation.
    args.artifact_directory.mkdir(mode=0o700)
    for name, content in serialized.items():
        with (args.artifact_directory / name).open("x", encoding="utf-8") as stream:
            stream.write(content)

    # Publish step outputs only after the complete bundle has been written.
    with args.github_output.open("a", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in outputs.items()))
    print(f"Exported {artifact_name}: evaluation_passed={outputs['evaluation_passed']}")


def main() -> int:
    try:
        export_results(parse_arguments())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Training/evaluation export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
