#!/usr/bin/env python3

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE_TAG = "gp-mof-capstone-training:dev"
DEFAULT_EXPERIMENT_NAME = "gp-mof-capstone-training"
DEFAULT_REGISTERED_MODEL_NAME = "gp-mof-capstone-model"
CONTAINER_DATASET_PATH = "/data/input/SeoulBikeData.csv"


class PreRunValidationError(Exception):
    """Raised when the local training run cannot be prepared."""


@dataclass(frozen=True)
class RunContext:
    image_tag: str
    image_digest: str
    mlflow_tracking_uri: str
    experiment_name: str
    docker_network: str
    registered_model_name: str
    training_data_path: Path
    n_estimators: int
    max_depth: int
    git_commit: str
    git_remote: str
    git_dirty: str


def get_environment_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as error:
        raise PreRunValidationError(f"Environment variable {name} must be an integer. Received: {value}") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the gp-mof-capstone training container.")

    parser.add_argument(
        "--training-data",
        default=os.getenv("TRAINING_DATA_HOST_PATH"),
        help=("Absolute WSL path of the dataset CSV file. Defaults to TRAINING_DATA_HOST_PATH."),
    )
    parser.add_argument(
        "--mlflow-tracking-uri",
        default=os.getenv("MLFLOW_TRACKING_URI"),
        help=("MLflow Tracking Server URI reachable from the training container. Defaults to MLFLOW_TRACKING_URI."),
    )
    parser.add_argument(
        "--docker-network",
        default=os.getenv("DOCKER_NETWORK"),
        help=("Docker network containing the MLflow service. Defaults to DOCKER_NETWORK."),
    )
    parser.add_argument(
        "--image-tag",
        default=os.getenv("IMAGE_TAG", DEFAULT_IMAGE_TAG),
    )
    parser.add_argument(
        "--experiment-name",
        default=os.getenv("MLFLOW_EXPERIMENT_NAME", DEFAULT_EXPERIMENT_NAME),
    )
    parser.add_argument(
        "--registered-model-name",
        default=os.getenv(
            "REGISTERED_MODEL_NAME",
            DEFAULT_REGISTERED_MODEL_NAME,
        ),
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=get_environment_int("N_ESTIMATORS", 100),
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=get_environment_int("MAX_DEPTH", 10),
    )

    return parser.parse_args()


def require_value(value: str | None, option_description: str) -> str:
    if not value:
        raise PreRunValidationError(
            f"{option_description} is required. "
            "Pass it as a command-line option or define the corresponding "
            "environment variable."
        )

    return value


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )

    return result.stdout.strip()


def get_git_metadata(repository_dir: Path) -> dict[str, str]:
    git_commit = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_dir,
    )

    git_remote = (
        run_command(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=repository_dir,
            check=False,
        )
        or "unknown"
    )

    git_status = run_command(
        ["git", "status", "--porcelain"],
        cwd=repository_dir,
    )

    return {
        "commit": git_commit,
        "remote": git_remote,
        "dirty": str(bool(git_status)).lower(),
    }


def get_image_digest(image_tag: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            '--format={{join .RepoDigests "\\n"}}',
            image_tag,
        ],
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        error_message = result.stderr.strip() or "Image was not found."
        raise PreRunValidationError(
            f"Docker image not found: {image_tag}. Build it before starting the training run. Details: {error_message}"
        )

    repo_digests = result.stdout.strip().splitlines()

    return repo_digests[0] if repo_digests else "unknown"


def resolve_run_context(args: argparse.Namespace) -> RunContext:
    training_data = require_value(
        args.training_data,
        "--training-data / TRAINING_DATA_HOST_PATH",
    )
    mlflow_tracking_uri = require_value(
        args.mlflow_tracking_uri,
        "--mlflow-tracking-uri / MLFLOW_TRACKING_URI",
    )
    docker_network = require_value(
        args.docker_network,
        "--docker-network / DOCKER_NETWORK",
    )

    training_data_path = Path(training_data).expanduser().resolve()

    if not training_data_path.is_file():
        raise PreRunValidationError(f"Training data file not found: {training_data_path}")

    if training_data_path.suffix.lower() != ".csv":
        raise PreRunValidationError(f"Training data must be a CSV file. Received: {training_data_path}")

    script_path = Path(__file__).resolve()
    repository_dir = script_path.parents[2]

    if not (repository_dir / ".git").exists():
        raise PreRunValidationError(
            f"Git repository metadata was not found at: {repository_dir}. Run the launcher from a Git working copy."
        )

    try:
        git_metadata = get_git_metadata(repository_dir)
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreRunValidationError(f"Unable to resolve Git metadata from: {repository_dir}") from error

    try:
        image_digest = get_image_digest(args.image_tag)
    except OSError as error:
        raise PreRunValidationError(
            "Unable to execute Docker. Verify that Docker Desktop is running "
            "and that the Docker CLI is available in WSL."
        ) from error

    return RunContext(
        image_tag=args.image_tag,
        image_digest=image_digest,
        mlflow_tracking_uri=mlflow_tracking_uri,
        experiment_name=args.experiment_name,
        docker_network=docker_network,
        registered_model_name=args.registered_model_name,
        training_data_path=training_data_path,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        git_commit=git_metadata["commit"],
        git_remote=git_metadata["remote"],
        git_dirty=git_metadata["dirty"],
    )


def build_docker_command(run_context: RunContext) -> list[str]:
    environment_variables = {
        "MLFLOW_TRACKING_URI": run_context.mlflow_tracking_uri,
        "MLFLOW_EXPERIMENT_NAME": run_context.experiment_name,
        "GIT_COMMIT": run_context.git_commit,
        "GIT_REMOTE": run_context.git_remote,
        "GIT_DIRTY": run_context.git_dirty,
        "CONTAINER_IMAGE": run_context.image_tag,
        "CONTAINER_IMAGE_DIGEST": run_context.image_digest,
    }

    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        run_context.docker_network,
        "--mount",
        (f"type=bind,src={run_context.training_data_path},dst={CONTAINER_DATASET_PATH},readonly"),
    ]

    for key, value in environment_variables.items():
        command.extend(["--env", f"{key}={value}"])

    command.extend(
        [
            run_context.image_tag,
            "--training_data",
            CONTAINER_DATASET_PATH,
            "--n_estimators",
            str(run_context.n_estimators),
            "--max_depth",
            str(run_context.max_depth),
            "--registered_model_name",
            run_context.registered_model_name,
        ]
    )

    return command


def print_run_summary(
    run_context: RunContext,
    docker_command: list[str],
) -> None:
    print("Training configuration")
    print(f"  Image:                  {run_context.image_tag}")
    print(f"  Image digest:           {run_context.image_digest}")
    print(f"  MLflow tracking URI:    {run_context.mlflow_tracking_uri}")
    print(f"  Docker network:         {run_context.docker_network}")
    print(f"  Experiment:             {run_context.experiment_name}")
    print(f"  Registered model:       {run_context.registered_model_name}")
    print(f"  Dataset host path:      {run_context.training_data_path}")
    print(f"  Dataset container path: {CONTAINER_DATASET_PATH}")
    print(f"  Git commit:             {run_context.git_commit}")
    print(f"  Git remote:             {run_context.git_remote}")
    print(f"  Git dirty:              {run_context.git_dirty}")
    print(f"  n_estimators:           {run_context.n_estimators}")
    print(f"  max_depth:              {run_context.max_depth}")
    print()
    print("Docker command")
    print(f"  {shlex.join(docker_command)}")
    print()


def execute_docker_command(docker_command: list[str]) -> None:
    try:
        subprocess.run(docker_command, check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error


def main() -> None:
    try:
        args = parse_args()
        run_context = resolve_run_context(args)
    except PreRunValidationError as error:
        print(f"Pre-run validation error: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    docker_command = build_docker_command(run_context)

    print_run_summary(
        run_context=run_context,
        docker_command=docker_command,
    )

    execute_docker_command(docker_command)


if __name__ == "__main__":
    main()
