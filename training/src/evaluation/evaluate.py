"""Evaluate a logged candidate against a fixed champion or bootstrap thresholds."""

import argparse
import json
import os
from pathlib import Path

from common.test_set_persistence import load_test_set

import mlflow
from evaluation.comparison import evaluate_bootstrap, evaluate_candidate


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-result", type=Path, required=True)
    parser.add_argument("--input-snapshot", type=Path, required=True)
    parser.add_argument("--test-set-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--bootstrap", action="store_true")
    return parser.parse_args()


def evaluate_training(
    training_result: Path,
    input_snapshot: Path,
    test_set_directory: Path,
    report_path: Path,
    expected_commit: str,
    bootstrap: bool = False,
) -> dict:
    # Bind local inputs to the workflow commit and reject stale output.
    if report_path.exists():
        raise ValueError(f"Evaluation report already exists: {report_path}")
    result = json.loads(training_result.read_text(encoding="utf-8"))
    snapshot = json.loads(input_snapshot.read_text(encoding="utf-8"))
    candidate = {key: result[key] for key in ("run_id", "model_uri", "git_commit")}
    if candidate["git_commit"] != expected_commit or snapshot["git_commit"] != expected_commit:
        raise ValueError("Evaluation inputs do not match the workflow commit.")
    if snapshot["mode"] != ("bootstrap" if bootstrap else "relative"):
        raise ValueError("Snapshot mode does not match the explicit evaluation mode.")
    if (snapshot["champion"] is None) != bootstrap:
        raise ValueError("Champion reference is inconsistent with the evaluation mode.")

    # Require a finished training run with the expected dataset provenance.
    client = mlflow.MlflowClient()
    run = client.get_run(candidate["run_id"])
    if (
        run.info.status != "FINISHED"
        or run.data.tags.get("git.commit") != expected_commit
        or run.data.tags.get("dataset.sha256") != snapshot["dataset"]["sha256"]
    ):
        raise ValueError("Candidate run is unfinished or does not match the snapshot provenance.")

    # Verify the exact logged model belongs to the supplied run, without resolving aliases.
    prefix = "models:/"
    if not candidate["model_uri"].startswith(prefix) or "/" in candidate["model_uri"][len(prefix) :]:
        raise ValueError("Expected the logged-model URI returned by training.")
    logged_model = client.get_logged_model(candidate["model_uri"][len(prefix) :])
    if logged_model.source_run_id != candidate["run_id"]:
        raise ValueError("Candidate model does not belong to the supplied training run.")

    # Apply the explicit gate on the verified persisted test set, without preprocessing.
    features, target = load_test_set(test_set_directory, result["test_set_manifest"], candidate, snapshot)
    if bootstrap:
        report = evaluate_bootstrap(candidate, result["test_set_manifest"]["test_set"], features, target)
    else:
        report = evaluate_candidate(
            candidate, snapshot["champion"], result["test_set_manifest"]["test_set"], features, target
        )

    # Persist either gate outcome without reopening the finished training run.
    client.log_dict(candidate["run_id"], report, "evaluation/report.json")
    client.set_tag(candidate["run_id"], "evaluation.passed", str(report["passed"]).lower())
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main() -> None:
    args = parse_arguments()
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    report = evaluate_training(
        args.training_result,
        args.input_snapshot,
        args.test_set_directory,
        args.report,
        args.expected_commit,
        bootstrap=args.bootstrap,
    )

    # Reject the workflow step only after the negative report has been saved.
    if not report["passed"]:
        reason = "bootstrap thresholds not met" if args.bootstrap else "candidate RMSE exceeds champion RMSE"
        raise SystemExit(f"Evaluation rejected: {reason}.")


if __name__ == "__main__":
    main()
