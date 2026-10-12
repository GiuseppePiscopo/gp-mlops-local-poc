#!/usr/bin/env python3
"""Render model-quality drift metrics as Prometheus exposition format."""

import argparse
import json
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drift", type=Path, required=True)
    parser.add_argument("--reference-lock", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    drift = json.loads(args.drift.read_text(encoding="utf-8"))
    lock = json.loads(args.reference_lock.read_text(encoding="utf-8"))

    labels = {
        "model": lock["registered_model"],
        "version": lock["resolved_version"],
        "source_run_id": lock["source_run_id"],
    }
    label_text = ",".join(f'{key}="{value}"' for key, value in labels.items())

    lines = [
        "# HELP model_quality_sample_count Number of inference records in the drift batch.",
        "# TYPE model_quality_sample_count gauge",
        f"model_quality_sample_count{{{label_text}}} {drift['sample_count']}",
        "# HELP model_quality_feature_psi Population Stability Index for one feature.",
        "# TYPE model_quality_feature_psi gauge",
    ]

    for feature, value in drift["features"].items():
        feature_labels = f'{label_text},feature="{feature}"'
        lines.append(f"model_quality_feature_psi{{{feature_labels}}} {value}")

    lines.extend(
        [
            "# HELP model_quality_output_psi Population Stability Index for model predictions.",
            "# TYPE model_quality_output_psi gauge",
            f"model_quality_output_psi{{{label_text}}} {drift['output_psi']}",
            "# HELP model_quality_max_feature_psi Maximum feature PSI in the drift batch.",
            "# TYPE model_quality_max_feature_psi gauge",
            f"model_quality_max_feature_psi{{{label_text}}} {drift['max_feature_psi']}",
        ]
    )

    print("\n".join(lines))


if __name__ == "__main__":
    main()
