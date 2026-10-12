import os
from pathlib import Path

import mlflow
from mlflow import MlflowClient

EXPECTED_ARTIFACT_CONTENT = "MLflow artifact proxy to local S3-compatible storage: OK\n"

tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
run_id = os.environ["MLFLOW_SMOKE_RUN_ID"]

mlflow.set_tracking_uri(tracking_uri)
client = MlflowClient()

run = client.get_run(run_id)

assert run.info.experiment_id
assert run.data.params["tracking_uri"] == "http://mlflow:5000"
assert run.data.tags["purpose"] == "infrastructure-smoke-test"
assert run.data.tags["scope"] == "setup-only-no-training"

artifact_file = mlflow.artifacts.download_artifacts(
    run_id=run_id,
    artifact_path="smoke-test.txt",
)

artifact_content = Path(artifact_file).read_text(encoding="utf-8")
assert artifact_content == EXPECTED_ARTIFACT_CONTENT

print(f"MLFLOW_SMOKE_RUN_EXISTS={run.info.run_id}")
print(f"MLFLOW_SMOKE_EXPERIMENT_ID={run.info.experiment_id}")
print(f"MLFLOW_SMOKE_ARTIFACT_READ_OK={artifact_file}")
