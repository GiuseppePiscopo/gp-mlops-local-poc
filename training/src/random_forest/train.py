"""Trains a RandomForestRegressor on the Seoul Bike Sharing dataset and logs the run
to the MLflow Tracking Server: hyperparameters, dataset/provenance tags, evaluation
metrics and the model signature. Model Registry version creation can be skipped.

The container only needs `MLFLOW_TRACKING_URI`, not SeaweedFS endpoint or
credentials: the Tracking Server's artifact root uses the `mlflow-artifacts:`
proxy scheme, so the client uploads the model over HTTP to the Tracking Server
itself, which forwards the request to SeaweedFS using its own credentials.
"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import mlflow.sklearn
import pandas as pd
from common.test_set_persistence import save_test_set
from common.training_snapshot import load_snapshot
from common.training_utils import RANDOM_SEED, TEST_SIZE, eval_model, get_data, prepare_data
from mlflow.models import infer_signature
from sklearn.ensemble import RandomForestRegressor

import mlflow

DEFAULT_EXPERIMENT_NAME = "gp-mof-capstone-training"
DEFAULT_REGISTERED_MODEL_NAME = "gp-mof-capstone-model"
MODEL_ARTIFACT_NAME = "model"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--training_data",
        type=str,
        required=True,
        help="Path to the data CSV file.",
    )
    parser.add_argument(
        "--n_estimators",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--max_depth",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--registered_model_name",
        type=str,
        default=DEFAULT_REGISTERED_MODEL_NAME,
    )
    parser.add_argument(
        "--skip_registration",
        action="store_true",
        help="Log the model without creating a Model Registry version.",
    )
    parser.add_argument(
        "--result_json", type=Path, help="Write the completed run and logged model identifiers as JSON."
    )
    parser.add_argument("--input_snapshot", type=Path, help="Use a previously acquired dataset and champion snapshot.")
    parser.add_argument("--test_set_directory", type=Path, help="Write the test set to this ephemeral directory.")
    parser.add_argument(
        "--fail_for_alert_test",
        type=lambda value: value.lower() == "true",
        default=False,
    )

    return parser.parse_args()


def get_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def get_dataset_tags(training_data: str, verified_dataset: dict | None = None) -> dict[str, str]:
    dataset_path = Path(training_data).resolve()

    if not dataset_path.is_file():
        raise ValueError(
            f"This training workflow expects --training_data to reference one CSV file. Received: {dataset_path}"
        )

    dataset_stat = dataset_path.stat()
    modified_at = datetime.fromtimestamp(
        dataset_stat.st_mtime,
        tz=UTC,
    ).isoformat()

    if verified_dataset is not None:
        size_bytes = verified_dataset["size_bytes"]
        sha256 = verified_dataset["sha256"]
    else:
        size_bytes = dataset_stat.st_size
        sha256 = get_file_sha256(dataset_path)

    return {
        "dataset.name": "gp-mof-capstone-data",
        "dataset.filename": dataset_path.name,
        "dataset.path": str(dataset_path),
        "dataset.size_bytes": str(size_bytes),
        "dataset.last_modified_utc": modified_at,
        "dataset.sha256": sha256,
    }


def get_provenance_tags() -> dict[str, str]:
    environment_tag_names = {
        "git.commit": "GIT_COMMIT",
        "git.remote": "GIT_REMOTE",
        "git.dirty": "GIT_DIRTY",
        "container.image": "CONTAINER_IMAGE",
        "container.image_id": "CONTAINER_IMAGE_ID",
    }

    tags = {
        "project.name": "gp-mof-capstone",
        "project.component": "training",
        "use_case": "seoul-bike-demand-forecast",
        "model.family": "RandomForestRegressor",
    }

    for mlflow_tag_name, environment_name in environment_tag_names.items():
        tags[mlflow_tag_name] = os.getenv(environment_name, "unknown")

    if image_digest := os.getenv("CONTAINER_IMAGE_DIGEST"):
        tags["container.image_digest"] = image_digest

    return tags


def train_model(
    n_estimators: int,
    max_depth: int | None,
    X_train,
    y_train,
) -> RandomForestRegressor:
    print("Training model...")

    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )

    return model.fit(X_train, y_train)


def main(args: argparse.Namespace) -> None:
    if args.input_snapshot is not None and args.test_set_directory is None:
        raise ValueError("--input_snapshot requires --test_set_directory.")
    if args.input_snapshot is not None and (not args.skip_registration or args.result_json is None):
        raise ValueError("Snapshot training requires --skip_registration and --result_json.")
    if args.test_set_directory is not None and args.input_snapshot is None:
        raise ValueError("--test_set_directory requires --input_snapshot.")
    if args.fail_for_alert_test:
        raise RuntimeError("ALERT TEST: intentional training job failure")

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RuntimeError("MLFLOW_TRACKING_URI environment variable is required")

    experiment_name = os.getenv(
        "MLFLOW_EXPERIMENT_NAME",
        DEFAULT_EXPERIMENT_NAME,
    )

    mlflow.set_tracking_uri(tracking_uri)
    dataset_bytes = None
    input_snapshot = None
    if args.input_snapshot is not None:
        dataset_bytes = Path(args.training_data).read_bytes()
        input_snapshot = load_snapshot(
            args.input_snapshot,
            dataset_bytes,
            os.environ["GIT_COMMIT"],
            args.registered_model_name,
        )
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run() as run:
        mlflow.set_tags(get_provenance_tags())
        dataset_tags = get_dataset_tags(
            args.training_data, input_snapshot["dataset"] if input_snapshot is not None else None
        )
        mlflow.set_tags(dataset_tags)
        if input_snapshot is not None:
            mlflow.log_dict(input_snapshot, "evaluation/input-snapshot.json")

        mlflow.log_params(
            {
                "n_estimators": args.n_estimators,
                "max_depth": args.max_depth,
                "random_seed": RANDOM_SEED,
                "test_size": TEST_SIZE,
                "target_column": "Rented Bike Count",
            }
        )

        df = pd.read_csv(BytesIO(dataset_bytes)) if dataset_bytes is not None else get_data(args.training_data)
        X_train, X_test, y_train, y_test = prepare_data(df)

        mlflow.log_params(
            {
                "dataset.rows": len(df),
                "dataset.columns": len(df.columns),
                "training.rows": len(X_train),
                "test.rows": len(X_test),
                "feature.count": len(X_train.columns),
            }
        )

        mlflow.log_dict(
            {"feature_names": X_train.columns.tolist()},
            "metadata/feature_names.json",
        )

        model = train_model(
            n_estimators=args.n_estimators,
            max_depth=args.max_depth,
            X_train=X_train,
            y_train=y_train,
        )

        eval_model(
            model=model,
            X_test=X_test,
            y_test=y_test,
        )

        input_example = X_train.head(3)
        signature = infer_signature(
            model_input=input_example,
            model_output=model.predict(input_example),
        )

        model_info = mlflow.sklearn.log_model(
            sk_model=model,
            name=MODEL_ARTIFACT_NAME,
            signature=signature,
            input_example=input_example,
            serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_SKOPS,
            skops_trusted_types=[
                "sklearn.tree._tree.Tree",
            ],
            registered_model_name=None if args.skip_registration else args.registered_model_name,
        )

        result: dict = {
            "run_id": run.info.run_id,
            "model_uri": model_info.model_uri,
            "git_commit": os.getenv("GIT_COMMIT", "unknown"),
        }
        if input_snapshot is not None:
            manifest = save_test_set(args.test_set_directory, X_test, y_test, result.copy(), input_snapshot)
            mlflow.log_dict(manifest, "evaluation/test-set-manifest.json")
            result["test_set_manifest"] = manifest

    if args.result_json is not None:
        args.result_json.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    print("\n" + "*" * 60)

    main(parse_args())

    print("*" * 60 + "\n")
