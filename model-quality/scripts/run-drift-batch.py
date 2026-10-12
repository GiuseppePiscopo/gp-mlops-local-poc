#!/usr/bin/env python3
"""Scheduled drift batch for CronJob execution.

Reads the last 26h of inference logs from the predictor pods via the
Kubernetes API (deduplicated by request_id), downloads the versioned
reference from S3, computes PSI metrics through compute-drift.py, renders
Prometheus exposition through render-drift-metrics.py and pushes the result
to Pushgateway. The batch day defaults to yesterday (UTC).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta

import boto3
from kubernetes import client, config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default="", help="Batch day YYYY-MM-DD (default: yesterday UTC)")
    return parser.parse_args()


def read_pod_logs(namespace: str, label: str) -> list[dict]:
    """Collect inference records from running predictor pods, deduplicated."""
    config.load_incluster_config()
    api = client.CoreV1Api()
    pods = api.list_namespaced_pod(namespace, label_selector=label)

    records = {}
    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        try:
            text = api.read_namespaced_pod_log(
                pod.metadata.name, namespace, container="kserve-container",
                since_seconds=26 * 3600, previous=False,
            )
        except client.ApiException as error:
            print(f"Log non leggibili da {pod.metadata.name}: {error}", file=sys.stderr)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "request_id" in record:
                records[record["request_id"]] = record
    return list(records.values())


def run_script(name: str, *args: str) -> None:
    """Run a sibling script, letting its own output through."""
    subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, name), *args], check=True
    )


def main() -> None:
    args = parse_arguments()
    day = args.day or (datetime.now(UTC) - timedelta(days=1)).strftime("%F")

    bucket = os.environ["LOG_BUCKET"]
    serving_namespace = os.environ.get("SERVING_NAMESPACE", "gp-mof-capstone-serving")
    pod_label = os.environ.get("POD_LABEL", "app=isvc.gp-mof-capstone-model-predictor")
    reference_prefix = os.environ.get("REFERENCE_PREFIX", "reference/model=v1")
    pushgateway_url = os.environ.get("PUSHGATEWAY_URL", "http://mlops-pushgateway.monitoring.svc.cluster.local:9091")

    records = read_pod_logs(serving_namespace, pod_label)
    print(f"Record correnti: {len(records)}")
    if not records:
        raise RuntimeError("Nessun record di inferenza nei log del predictor")

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    with tempfile.TemporaryDirectory(prefix="drift-batch-") as tmp_dir:
        input_path = os.path.join(tmp_dir, "current.jsonl")
        with open(input_path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

        reference_dir = os.path.join(tmp_dir, "reference")
        os.mkdir(reference_dir)
        for name in ("reference-stats.json", "reference.lock.json"):
            body = s3.get_object(
                Bucket=bucket, Key=f"{reference_prefix}/{name}"
            )["Body"].read()
            with open(os.path.join(reference_dir, name), "wb") as handle:
                handle.write(body)
        lock = json.loads(
            open(os.path.join(reference_dir, "reference.lock.json"), encoding="utf-8").read()
        )
        print(f"Reference: {lock['registered_model']} v{lock['resolved_version']}")

        drift_path = os.path.join(tmp_dir, "drift.json")
        run_script(
            "compute-drift.py",
            "--input", input_path,
            "--reference", reference_dir,
            "--output", drift_path,
        )

        body = subprocess.run(
            [
                sys.executable, os.path.join(SCRIPT_DIR, "render-drift-metrics.py"),
                "--drift", drift_path,
                "--reference-lock", os.path.join(reference_dir, "reference.lock.json"),
                "--instance", day,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.encode("utf-8")

    url = f"{pushgateway_url}/metrics/job/model_quality_drift/instance/{day}"
    request = urllib.request.Request(url, data=body, method="PUT")
    with urllib.request.urlopen(request, timeout=30) as response:
        print(f"Push: {response.status}")

    print(f"Batch {day}: {len(records)} record, push completato")


if __name__ == "__main__":
    main()
