#!/usr/bin/env python3

"""Launcher that validates inputs and runs the training container with `docker run`.

This script runs from the WSL operational copy of `training/` (no `.git/` there), so
the authoritative Git working tree is passed explicitly via `--git-repository` /
`GIT_REPOSITORY`. Before invoking Docker, it checks that the dataset CSV exists, that
the Docker image was already built, and that the given path is a valid Git working
tree; it then reads the current commit, remote URL and dirty state from that
repository and forwards them to the training container as MLflow run tags. Any
failed check raises `PreRunValidationError` and exits with status 2 without
starting Docker.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXPERIMENT_NAME = "gp-mof-capstone-training"
DEFAULT_REGISTERED_MODEL_NAME = "gp-mof-capstone-model"
CONTAINER_DATASET_PATH = "/data/input/SeoulBikeData.csv"


class PreRunValidationError(Exception):
    """Raised when the local training run cannot be prepared."""


@dataclass(frozen=True)
class RunContext:
    image_tag: str
    image_id: str
    image_digest: str
    mlflow_tracking_uri: str
    experiment_name: str
    docker_network: str
    registered_model_name: str
    training_data_path: Path
    git_repository_path: Path
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
        "--git-repository",
        default=os.getenv("GIT_REPOSITORY"),
        help=(
            "Absolute WSL path of the authoritative Git repository used to "
            "resolve commit, remote URL and working-tree state. "
            "Defaults to GIT_REPOSITORY."
        ),
    )
    parser.add_argument(
        "--image-tag",
        default=os.getenv("IMAGE_TAG"),
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


def validate_git_repository(git_repository_path: Path) -> None:
    if not git_repository_path.is_dir():
        raise PreRunValidationError(f"Git repository directory not found: {git_repository_path}")

    try:
        is_git_repository = run_command(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=git_repository_path,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreRunValidationError(f"Unable to execute Git against repository: {git_repository_path}") from error

    if is_git_repository.lower() != "true":
        raise PreRunValidationError(f"Path is not a Git working tree: {git_repository_path}")


def get_git_metadata(git_repository_path: Path) -> dict[str, str]:
    git_commit = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repository_path,
    )

    git_remote = (
        run_command(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=git_repository_path,
            check=False,
        )
        or "unknown"
    )

    git_status = run_command(
        ["git", "status", "--porcelain"],
        cwd=git_repository_path,
    )

    return {
        "commit": git_commit,
        "remote": git_remote,
        "dirty": str(bool(git_status)).lower(),
    }


def get_image_identity(git_repository: Path, image_tag: str | None) -> dict:
    arguments = [
        sys.executable,
        str(git_repository / "ci-cd" / "scripts" / "prepare_image.py"),
        "resolve",
        "--repository",
        str(git_repository),
        "--component",
        "training",
    ]
    if image_tag:
        arguments.extend(["--image", image_tag])
    result = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        error_message = result.stderr.strip() or "Image resolution failed."
        raise PreRunValidationError(
            f"Unable to validate training image. Build it from the committed sources first. Details: {error_message}"
        )

    try:
        identity = json.loads(result.stdout)
    except ValueError as error:
        raise PreRunValidationError("Invalid image provenance JSON.") from error
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("image"), str)
        or not isinstance(identity.get("image_id"), str)
        or not isinstance(identity.get("repo_digests"), list)
        or not all(isinstance(value, str) for value in identity["repo_digests"])
    ):
        raise PreRunValidationError("Incomplete image provenance.")
    return identity


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
    git_repository = require_value(
        args.git_repository,
        "--git-repository / GIT_REPOSITORY",
    )

    training_data_path = Path(training_data).expanduser().resolve()
    git_repository_path = Path(git_repository).expanduser().resolve()

    if not training_data_path.is_file():
        raise PreRunValidationError(f"Training data file not found: {training_data_path}")

    if training_data_path.suffix.lower() != ".csv":
        raise PreRunValidationError(f"Training data must be a CSV file. Received: {training_data_path}")

    validate_git_repository(git_repository_path)

    try:
        git_metadata = get_git_metadata(git_repository_path)
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreRunValidationError(f"Unable to resolve Git metadata from: {git_repository_path}") from error

    try:
        identity = get_image_identity(git_repository_path, args.image_tag)
    except OSError as error:
        raise PreRunValidationError(
            "Unable to execute Docker. Verify that Docker Desktop is running "
            "and that the Docker CLI is available in WSL."
        ) from error

    if identity.get("git_commit") != git_metadata["commit"] or git_metadata["dirty"] != "false":
        raise PreRunValidationError(
            "Image revision and clean Git checkout must match the recorded training provenance."
        )

    return RunContext(
        image_tag=identity["image"],
        image_id=identity["image_id"],
        image_digest=identity["repo_digests"][0] if identity["repo_digests"] else "",
        mlflow_tracking_uri=mlflow_tracking_uri,
        experiment_name=args.experiment_name,
        docker_network=docker_network,
        registered_model_name=args.registered_model_name,
        training_data_path=training_data_path,
        git_repository_path=git_repository_path,
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
        "CONTAINER_IMAGE_ID": run_context.image_id,
    }
    if run_context.image_digest:
        environment_variables["CONTAINER_IMAGE_DIGEST"] = run_context.image_digest

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
            run_context.image_id,
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
    print(f"  Local image ID:         {run_context.image_id}")
    print(f"  Registry RepoDigest:    {run_context.image_digest or '(not available for this local image)'}")
    print(f"  MLflow tracking URI:    {run_context.mlflow_tracking_uri}")
    print(f"  Docker network:         {run_context.docker_network}")
    print(f"  Experiment:             {run_context.experiment_name}")
    print(f"  Registered model:       {run_context.registered_model_name}")
    print(f"  Dataset host path:      {run_context.training_data_path}")
    print(f"  Dataset container path: {CONTAINER_DATASET_PATH}")
    print(f"  Git repository:         {run_context.git_repository_path}")
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
