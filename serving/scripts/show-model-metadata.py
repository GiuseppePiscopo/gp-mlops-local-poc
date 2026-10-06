#!/usr/bin/env python3
"""Print a short summary of a V2 model metadata JSON file."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path,
                        help="V2 model metadata JSON returned by the server")
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    inputs = metadata["inputs"]

    print(f"{metadata['name']} {len(inputs)} inputs")
    for item in inputs:
        print(f"  {item['name']} {item['datatype']}")


if __name__ == "__main__":
    main()
