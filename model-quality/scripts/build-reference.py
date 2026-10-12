#!/usr/bin/env python3
"""Build the versioned reference dataset for model quality monitoring.

Resolves the model alias in the MLflow Model Registry, verifies the training
CSV against the dataset provenance tags of the source run, recomputes the
model predictions and writes per-feature reference statistics plus a
provenance lock into the output directory.
"""

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from mlflow.tracking import MlflowClient

import mlflow

QUANTILES = [round(q / 10, 2) for q in range(11)]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gp-mof-capstone-model")
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--dataset", type=Path, required=True, help="Training CSV file")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def resolve_alias(client: MlflowClient, model: str, alias: str):
    """Resolve the alias to a model version and its source run."""
    version = client.get_model_version_by_alias(model, alias)
    run = client.get_run(version.run_id)
    return version, run


def verify_dataset(dataset: Path, expected_sha: str) -> str:
    """Check the CSV against the dataset provenance tag."""
    actual_sha = sha256_of(dataset)
    if actual_sha != expected_sha:
        raise RuntimeError(f"Dataset mismatch: file sha256 {actual_sha} != tag {expected_sha}")
    print(f"Dataset verificato: {dataset.name} sha256 {actual_sha[:12]}...")
    return actual_sha


def load_feature_names(client: MlflowClient, run_id: str) -> list:
    """Download the feature list logged by the training run."""
    with tempfile.TemporaryDirectory(prefix="reference-features-") as tmp_dir:
        local_path = client.download_artifacts(
            run_id,
            "metadata/feature_names.json",
            tmp_dir,
        )
        payload = json.loads(Path(local_path).read_text(encoding="utf-8"))
        feature_names = payload["feature_names"]

    print(f"Feature dal run: {len(feature_names)}")
    return feature_names


def prepare_reference_features(df: pd.DataFrame, feature_names: list) -> pd.DataFrame:
    """Apply the same preprocessing used by the training pipeline."""
    df = df.drop(columns=["Date"])
    df = pd.get_dummies(
        df,
        columns=["Seasons", "Holiday", "Functioning Day"],
        drop_first=True,
    )

    missing = [name for name in feature_names if name not in df.columns]
    if missing:
        raise RuntimeError(f"Colonne mancanti dopo il preprocessing: {missing}")

    # Garantisce ordine e presenza esatta delle feature del modello.
    return df[feature_names].astype(float)


def compute_stats(df: pd.DataFrame, feature_names: list, predictions) -> dict:
    """Per-feature quantile bin edges plus output distribution quantiles."""
    features = {
        name: {
            "count": int(df[name].count()),
            "bin_edges": [float(v) for v in df[name].quantile(QUANTILES).tolist()],
        }
        for name in feature_names
    }
    output = {
        "count": int(len(predictions)),
        "quantiles": [float(v) for v in pd.Series(predictions).quantile(QUANTILES).tolist()],
    }
    return {"features": features, "output": output}


def write_outputs(output_dir: Path, model: str, alias: str, version, run, sha: str, rows: int, stats: dict) -> None:
    """Write reference statistics and provenance lock."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reference-stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    lock = {
        "registered_model": model,
        "requested_alias": alias,
        "resolved_version": version.version,
        "source_run_id": version.run_id,
        "dataset": {
            "name": run.data.tags.get("dataset.name"),
            "sha256": sha,
            "rows": rows,
        },
        "built_at": datetime.now(UTC).isoformat(),
        "files": ["reference-stats.json"],
    }
    (output_dir / "reference.lock.json").write_text(json.dumps(lock, indent=1), encoding="utf-8")
    print(f"Reference scritta in {output_dir}: versione {version.version}")


def main() -> None:
    args = parse_arguments()

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RuntimeError("MLFLOW_TRACKING_URI environment variable is required")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()

    version, run = resolve_alias(client, args.model, args.alias)

    expected_sha = run.data.tags.get("dataset.sha256")
    if not expected_sha:
        raise RuntimeError(f"Run {version.run_id} has no dataset.sha256 tag")
    actual_sha = verify_dataset(args.dataset, expected_sha)

    feature_names = load_feature_names(client, version.run_id)

    model = mlflow.sklearn.load_model(f"models:/{args.model}/{version.version}")
    df = pd.read_csv(args.dataset)
    X_reference = prepare_reference_features(df, feature_names)

    predictions = model.predict(X_reference)
    print(f"Predizioni ricalcolate: {len(predictions)}")

    stats = compute_stats(X_reference, feature_names, predictions)
    write_outputs(args.output_dir, args.model, args.alias, version, run, actual_sha, len(X_reference), stats)


if __name__ == "__main__":
    main()
