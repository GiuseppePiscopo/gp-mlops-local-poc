"""Validate structured inference log records from stdin.

Reads JSON lines (e.g. piped from `kubectl logs`), keeps the flat JSONL
inference records and checks the minimal schema:

    {"timestamp", "model", "version", "request_id", "inputs", "outputs"}

Exits 1 when no valid record is found.
"""

import json
import sys

REQUIRED_FIELDS = {"timestamp", "model", "version", "request_id", "inputs", "outputs"}


def main() -> int:
    json_lines = 0
    valid = []
    for line in sys.stdin:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        json_lines += 1
        if REQUIRED_FIELDS <= set(record):
            valid.append(record)

    print(f"righe JSON: {json_lines}")
    print(f"record validi: {len(valid)}")

    if not valid:
        print("nessun record: generare traffico e riprovare")
        return 1

    last = valid[-1]
    print(
        f"ultimo: {last['model']} v{last['version']} "
        f"input: {len(last['inputs'])} output: {len(last['outputs'])}"
    )
    assert last["model"] and last["timestamp"] and last["request_id"], "campi base mancanti"
    print("schema OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
