#!/usr/bin/env python3
"""Send perturbed inference requests through Envoy Gateway.

Applies fixed offsets to named V2 inputs of a base request file, to produce
traffic that exhibits data drift (and induced prediction drift) against the
reference dataset. Prints one HTTP status per request.

Example:
  send-perturbed-traffic.py --request /opt/mlops-local-poc/serving/runtime/infer-request.json \
    --count 200 --shift 'Temperature(°C)=10' --shift Hour=12
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True, help="Base V2 infer request JSON")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument(
        "--shift", action="append", default=[],
        help="Input shift as NAME=OFFSET, repeatable (e.g. 'Temperature(°C)=10')",
    )
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="gp-mof-capstone-model")
    parser.add_argument("--namespace", default="gp-mof-capstone-serving")
    return parser.parse_args()


def parse_shifts(raw_shifts: list) -> dict:
    shifts = {}
    for raw in raw_shifts:
        try:
            name, offset = raw.split("=", 1)
            shifts[name.strip()] = float(offset)
        except ValueError:
            raise RuntimeError(f"Shift non valido (atteso NOME=OFFSET): {raw}")
    return shifts


def main() -> None:
    args = parse_arguments()
    shifts = parse_shifts(args.shift)

    payload = json.loads(args.request.read_text(encoding="utf-8"))
    names = {item["name"] for item in payload["inputs"]}
    unknown = [name for name in shifts if name not in names]
    if unknown:
        raise RuntimeError(f"Input non presenti nella richiesta: {unknown}")

    for item in payload["inputs"]:
        if item["name"] in shifts:
            offset = shifts[item["name"]]
            item["data"] = [[value[0] + offset] for value in item["data"]]

    body = json.dumps(payload).encode("utf-8")
    host = f"{args.model}-{args.namespace}.example.com"
    url = f"{args.gateway_url}/v2/models/{args.model}/infer"

    for index in range(1, args.count + 1):
        request = urllib.request.Request(
            url, data=body,
            headers={"Host": host, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                print(f"richiesta {index}/{args.count}: {response.status}")
        except Exception as error:
            print(f"richiesta {index}/{args.count}: errore {error}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
