#!/usr/bin/env python3
"""Build a V2 inference request from the MLflow input example of a model."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True,
                        help="V2 model metadata JSON returned by the server")
    parser.add_argument("--example", type=Path, required=True,
                        help="serving_input_example.json saved with the model")
    parser.add_argument("--output", type=Path, required=True,
                        help="V2 inference request JSON to write")
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    example = json.loads(args.example.read_text(encoding="utf-8"))["dataframe_split"]
    columns, rows = example["columns"], example["data"]

    # One V2 tensor per model input, shaped [rows, 1], in server-declared order.
    inputs = [
        {
            "name": item["name"],
            "datatype": item["datatype"],
            "shape": [len(rows), 1],
            "data": [[row[columns.index(item["name"])]] for row in rows],
        }
        for item in metadata["inputs"]
    ]

    args.output.write_text(json.dumps({"inputs": inputs}), encoding="utf-8")
    print(f"Request written: {args.output} ({len(rows)} rows, {len(inputs)} inputs)")


if __name__ == "__main__":
    main()
