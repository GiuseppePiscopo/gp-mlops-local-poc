#!/usr/bin/env python3
"""Compute unsupervised feature and prediction drift against a reference snapshot."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

EPSILON = 1e-6


def read_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line.startswith("{"):
                records.append(json.loads(line))
    return records


def flatten_records(records: list[dict]) -> tuple[pd.DataFrame, pd.Series]:
    rows = []
    predictions = []

    for record in records:
        batch_size = len(record["outputs"][0]["data"])
        row = {}

        for input_spec in record["inputs"]:
            values = [value[0] for value in input_spec["data"]]
            if len(values) != batch_size:
                raise RuntimeError(f"Input {input_spec['name']} has an inconsistent batch size")
            row[input_spec["name"]] = values

        output_values = record["outputs"][0]["data"]
        if len(output_values) != batch_size:
            raise RuntimeError("Output has an inconsistent batch size")

        for index in range(batch_size):
            rows.append({name: values[index] for name, values in row.items()})
            predictions.append(output_values[index])

    return pd.DataFrame(rows), pd.Series(predictions, dtype=float)


def psi(expected_counts: np.ndarray, actual_counts: np.ndarray) -> float:
    expected = expected_counts / expected_counts.sum()
    actual = actual_counts / actual_counts.sum()

    expected = np.clip(expected, EPSILON, None)
    actual = np.clip(actual, EPSILON, None)

    return float(np.sum((actual - expected) * np.log(actual / expected)))


def feature_psi(reference_edges: list[float], values: pd.Series) -> float:
    edges = np.asarray(reference_edges, dtype=float)

    # Binary features have degenerate quantile bins; compare category frequencies.
    if len(np.unique(edges)) <= 2:
        categories = np.unique(edges)
        expected_counts = np.array([np.sum(np.isclose(edges, category)) for category in categories])
        actual_counts = np.array([np.sum(np.isclose(values.astype(float), category)) for category in categories])
        return psi(expected_counts, actual_counts)

    clipped = np.clip(values.astype(float), edges[0], edges[-1])
    expected_counts, _ = np.histogram(edges[:-1], bins=edges)
    actual_counts, _ = np.histogram(clipped, bins=edges)
    return psi(expected_counts, actual_counts)


def output_psi(reference_quantiles: list[float], values: pd.Series) -> float:
    edges = np.asarray(reference_quantiles, dtype=float)
    clipped = np.clip(values, edges[0], edges[-1])
    expected_counts, _ = np.histogram(edges[:-1], bins=edges)
    actual_counts, _ = np.histogram(clipped, bins=edges)
    return psi(expected_counts, actual_counts)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Current inference JSONL file")
    parser.add_argument("--reference", type=Path, required=True, help="Directory containing reference-stats.json")
    parser.add_argument("--output", type=Path, required=True, help="Drift metrics JSON output")
    return parser.parse_args()


def compute_drift_metrics(reference: dict, records: list[dict]) -> dict:
    """Compute sample count and PSI metrics of records against the reference."""
    if not records:
        raise RuntimeError("Nessun record di inferenza nel file di input")

    current_features, current_predictions = flatten_records(records)

    feature_metrics = {}
    for name, spec in reference["features"].items():
        if name not in current_features.columns:
            raise RuntimeError(f"Feature mancante nei log correnti: {name}")
        feature_metrics[name] = feature_psi(spec["bin_edges"], current_features[name])

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_count": int(len(current_predictions)),
        "features": feature_metrics,
        "output_psi": output_psi(reference["output"]["quantiles"], current_predictions),
        "max_feature_psi": max(feature_metrics.values()),
    }


def main() -> None:
    args = parse_arguments()

    reference = json.loads((args.reference / "reference-stats.json").read_text(encoding="utf-8"))
    records = read_records(args.input)
    metrics = compute_drift_metrics(reference, records)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Record correnti: {metrics['sample_count']}")
    print(f"PSI output: {metrics['output_psi']:.6f}")
    print(f"PSI massimo feature: {metrics['max_feature_psi']:.6f}")
    print(f"Drift scritto in: {args.output}")


if __name__ == "__main__":
    main()
