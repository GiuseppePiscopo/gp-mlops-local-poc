"""Compare compatible sklearn regression models on one fixed test set."""

import math

import mlflow.sklearn
import pandas as pd
from common import training_utils


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
