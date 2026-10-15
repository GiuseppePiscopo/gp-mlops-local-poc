#!/usr/bin/env python3
"""Filter structured inference log records from stdin into a JSONL file.

Keeps parsed JSON lines carrying a request_id, one record per line.
Prints the extracted record count to stdout.

NOTE: the filtering logic here and in read_pod_logs() of run-drift-batch.py
must remain aligned.
"""

import argparse
import json
import sys
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="JSONL file to write")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    count = 0
    with args.output.open("w", encoding="utf-8") as out:
        for line in sys.stdin:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "request_id" not in record:
                continue
            out.write(json.dumps(record) + "\n")
            count += 1

    print(f"record estratti: {count}")


if __name__ == "__main__":
    main()
