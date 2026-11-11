"""Snapshot the reference model and evaluate both models on one fixed test set."""

import hashlib
import math
from pathlib import Path

import mlflow.sklearn
import pandas as pd

from common import training_utils
from mlflow import MlflowClient


def snapshot_champion(client: MlflowClient, registered_model: str) -> dict:
    version = client.get_model_version_by_alias(registered_model, "champion")
    if version.status != "READY" or not version.run_id or not version.source:
        raise ValueError("Champion must be READY with a source run and model URI.")
    if client.get_run(version.run_id).info.status != "FINISHED":
        raise ValueError("Champion source run must be FINISHED.")
    return {
        "registered_model": registered_model,
        "version": str(version.version),
        "run_id": version.run_id,
        "source": version.source,
        "model_uri": f"models:/{registered_model}/{version.version}",
    }


def test_set_identity(dataset_sha256: str, features: pd.DataFrame, target: pd.Series) -> dict:
    values = pd.concat([features, target], axis=1)
    return {
        "dataset_sha256": dataset_sha256,
        "random_seed": training_utils.RANDOM_SEED,
        "test_size": training_utils.TEST_SIZE,
        "preprocessing_sha256": hashlib.sha256(Path(training_utils.__file__).read_bytes()).hexdigest(),
        "feature_names": features.columns.tolist(),
        "feature_dtypes": [str(dtype) for dtype in features.dtypes],
        "feature_columns_name": features.columns.name,
        "target_name": target.name,
        "target_dtype": str(target.dtype),
        "index_name": features.index.name,
        "index_dtype": str(features.index.dtype),
        "test_row_indices": features.index.tolist(),
        "test_values_sha256": hashlib.sha256(
            pd.util.hash_pandas_object(values, index=True).values.tobytes()
        ).hexdigest(),
        "test_rows": len(features),
    }


def evaluate_model(model_uri: str, features: pd.DataFrame, target: pd.Series) -> dict[str, float]:
    model = mlflow.sklearn.load_model(model_uri)
    if not hasattr(model, "feature_names_in_") or list(model.feature_names_in_) != features.columns.tolist():
        raise ValueError(f"Model features do not match the test set: {model_uri}")
    # Avoid thread-dependent floating-point summation in a strict, zero-tolerance gate.
    if "n_jobs" in model.get_params(deep=False):
        model.set_params(n_jobs=1)
    metrics = training_utils.calculate_metrics(model, features, target)
    if set(metrics) != {"rmse", "mae", "r2", "mape"} or any(not math.isfinite(value) for value in metrics.values()):
        raise ValueError(f"Missing or non-finite evaluation metrics: {model_uri}")
    return metrics


def evaluate_candidate(
    candidate: dict, champion: dict, test_set: dict, features: pd.DataFrame, target: pd.Series
) -> dict:
    candidate_metrics = evaluate_model(candidate["model_uri"], features, target)
    champion_metrics = evaluate_model(champion["model_uri"], features, target)
    return {
        "mode": "relative",
        "candidate": candidate,
        "champion": champion,
        "test_set": test_set,
        "candidate_metrics": candidate_metrics,
        "champion_metrics": champion_metrics,
        "passed": candidate_metrics["rmse"] <= champion_metrics["rmse"],
    }
