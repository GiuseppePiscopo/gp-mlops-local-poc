#!/usr/bin/env python3
"""Recheck Registry provenance and promote only the successfully verified version."""

import argparse
import importlib.util
import json
import os
from pathlib import Path

import mlflow

SPEC = importlib.util.spec_from_file_location("deployment_checks", Path(__file__).with_name("check-deployment.py"))
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", type=Path, required=True)
    for name in (
        "registered-model",
        "model-version",
        "model-uri",
        "run-id",
        "expected-commit",
        "evaluation-mode",
        "event-name",
        "git-ref",
    ):
        parser.add_argument(f"--{name}", required=True)
    return parser.parse_args()


def promote_champion(args: argparse.Namespace) -> dict:
    # Bind the successful pod/inference verification to the exact approved target.
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    expected = {
        "status": "VERIFIED",
        "registered_model": args.registered_model,
        "model_version": args.model_version,
        "model_uri": args.model_uri,
        "run_id": args.run_id,
        "git_commit": args.expected_commit,
    }
    if any(verification[key] != value for key, value in expected.items()):
        raise ValueError("Deployment verification does not identify the approved model and successful smoke test.")

    # Reuse the pre-deployment checks immediately before the only Registry mutation.
    checked = checks.check_deployment(args)
    client = mlflow.MlflowClient()
    client.set_registered_model_alias(args.registered_model, "champion", args.model_version)
    champion = client.get_model_version_by_alias(args.registered_model, "champion")
    if (
        str(champion.version) != args.model_version
        or champion.run_id != args.run_id
        or champion.source != args.model_uri
    ):
        raise ValueError("Champion promotion could not be confirmed; inspect Registry state before retrying.")
    return {
        **checked,
        "previous_champion": checked["champion"]["version"] if checked["champion"] else None,
        "status": "PROMOTED",
    }


def main() -> None:
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    print(json.dumps(promote_champion(parse_arguments()), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
