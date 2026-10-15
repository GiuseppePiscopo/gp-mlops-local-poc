#!/usr/bin/env python3
"""Scheduled drift batch for CronJob execution.

Reads the last 26h of inference logs from the predictor pods via the
Kubernetes API (deduplicated by request_id), downloads the versioned
reference from S3, computes PSI metrics through compute-drift.py, renders
Prometheus exposition through render-drift-metrics.py and pushes the result
to Pushgateway. The batch day defaults to yesterday (UTC).
"""

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import boto3
from kubernetes import client, config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_sibling(name: str, filename: str):
    """Import a hyphenated sibling script by file path."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPT_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compute_drift = load_sibling("compute_drift", "compute-drift.py")
render_drift_metrics = load_sibling("render_drift_metrics", "render-drift-metrics.py")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default="", help="Batch day YYYY-MM-DD (default: yesterday UTC)")
    return parser.parse_args()


def read_pod_logs(namespace: str, label: str) -> list[dict]:
    """Collect inference records from running predictor pods, deduplicated."""
    # NOTE: the filtering logic here and in filter-log-records.py (used by export-logs.sh)
    # must remain aligned.
    config.load_incluster_config()
    api = client.CoreV1Api()
    pods = api.list_namespaced_pod(namespace, label_selector=label)

    records = {}
    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        try:
            text = api.read_namespaced_pod_log(
                pod.metadata.name,
                namespace,
                container="kserve-container",
                since_seconds=26 * 3600,
                previous=False,
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


def read_s3_json(s3, bucket: str, key: str) -> dict:
    """Download a JSON object from S3 and parse it."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


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

    reference = read_s3_json(s3, bucket, f"{reference_prefix}/reference-stats.json")
    lock = read_s3_json(s3, bucket, f"{reference_prefix}/reference.lock.json")

    print(f"Reference: {lock['registered_model']} v{lock['resolved_version']}")

    metrics = compute_drift.compute_drift_metrics(reference, records)

    print(f"PSI output: {metrics['output_psi']:.6f}")
    print(f"PSI massimo feature: {metrics['max_feature_psi']:.6f}")

    body = render_drift_metrics.render_exposition(metrics, lock, day).encode("utf-8")

    url = f"{pushgateway_url}/metrics/job/model_quality_drift/instance/{day}"
    request = urllib.request.Request(url, data=body, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"Push: {response.status}")
    except urllib.error.HTTPError as error:
        print(f"Push fallito: {error} -- {error.read().decode()[:500]}", file=sys.stderr)
        raise

    print(f"Batch {day}: {len(records)} record, push completato")


if __name__ == "__main__":
    main()
