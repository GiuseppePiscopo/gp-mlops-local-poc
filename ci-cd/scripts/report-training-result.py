#!/usr/bin/env python3
"""Append training identifiers and relative evaluation results to a job summary."""

import argparse
import json
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    identifiers = {key: result[key] for key in ("run_id", "model_uri", "git_commit")}
    report = result["evaluation"]
    text = (
        "## Training result\n\n```json\n"
        + json.dumps(identifiers, indent=2)
        + "\n```\n\n## Relative evaluation\n\n"
        + f"- Champion: `{report['champion']['registered_model']}` version `{report['champion']['version']}`\n"
        + f"- Champion run: `{report['champion']['run_id']}`\n"
        + f"- Dataset SHA-256: `{report['test_set']['dataset_sha256']}`\n"
        + f"- Test values SHA-256: `{report['test_set']['test_values_sha256']}`\n"
        + f"- Test rows: `{report['test_set']['test_rows']}`\n\n"
        + "| Metric | Candidate | Champion |\n|---|---|---|\n"
    )
    for metric in ("rmse", "mae", "r2", "mape"):
        text += f"| {metric} | {report['candidate_metrics'][metric]} | {report['champion_metrics'][metric]} |\n"
    text += f"\n- Passed: `{report['passed']}`\n"
    with args.summary.open("a", encoding="utf-8") as stream:
        stream.write(text)


if __name__ == "__main__":
    main()
