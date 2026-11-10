"""Acquire the champion reference and a CSV copy before training."""

import argparse
import json
import os
from pathlib import Path

from common.training_snapshot import prepare_snapshot

from mlflow import MlflowClient


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-data", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--registered-model", required=True)
    parser.add_argument("--git-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
    snapshot = prepare_snapshot(
        MlflowClient(tracking_uri=tracking_uri),
        args.registered_model,
        args.training_data,
        args.output_directory,
        args.git_commit,
    )
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    main()
