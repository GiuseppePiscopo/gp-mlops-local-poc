import argparse
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import mlflow.sklearn
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


def get_dataset_tags(training_data: str) -> dict[str, str]:
    dataset_path = Path(training_data).resolve()

    if not dataset_path.is_file():
        raise ValueError(
            f"This training workflow expects --training_data to reference one CSV file. Received: {dataset_path}"
        )

    modified_at = datetime.fromtimestamp(
        dataset_path.stat().st_mtime,
        tz=UTC,
    ).isoformat()

    return {
        "dataset.name": "gp-mof-capstone-data",
        "dataset.filename": dataset_path.name,
        "dataset.path": str(dataset_path),
        "dataset.size_bytes": str(dataset_path.stat().st_size),
        "dataset.last_modified_utc": modified_at,
        "dataset.sha256": get_file_sha256(dataset_path),
    }


def get_provenance_tags() -> dict[str, str]:
    environment_tag_names = {
        "git.commit": "GIT_COMMIT",
        "git.remote": "GIT_REMOTE",
        "git.dirty": "GIT_DIRTY",
        "container.image": "CONTAINER_IMAGE",
        "container.image_digest": "CONTAINER_IMAGE_DIGEST",
    }

    tags = {
        "project.name": "gp-mof-capstone",
        "project.component": "training",
        "use_case": "seoul-bike-demand-forecast",
        "model.family": "RandomForestRegressor",
    }

    for mlflow_tag_name, environment_name in environment_tag_names.items():
        tags[mlflow_tag_name] = os.getenv(environment_name, "unknown")

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
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run():
        mlflow.set_tags(get_provenance_tags())
        mlflow.set_tags(get_dataset_tags(args.training_data))

        mlflow.log_params(
            {
                "n_estimators": args.n_estimators,
                "max_depth": args.max_depth,
                "random_seed": RANDOM_SEED,
                "test_size": TEST_SIZE,
                "target_column": "Rented Bike Count",
            }
        )

        df = get_data(args.training_data)
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

        mlflow.sklearn.log_model(
            sk_model=model,
            name=MODEL_ARTIFACT_NAME,
            signature=signature,
            input_example=input_example,
            serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_SKOPS,
            skops_trusted_types=[
                "sklearn.tree._tree.Tree",
            ],
            registered_model_name=args.registered_model_name,
        )


if __name__ == "__main__":
    print("\n" + "*" * 60)

    main(parse_args())

    print("*" * 60 + "\n")
