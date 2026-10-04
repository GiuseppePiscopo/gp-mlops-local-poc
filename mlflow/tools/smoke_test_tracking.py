import os

import mlflow

TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
EXPERIMENT_NAME = "__platform_smoke_test__"
RUN_NAME = "artifact-proxy-smoke-test"
ARTIFACT_PATH = "smoke-test.txt"

mlflow.set_tracking_uri(TRACKING_URI)
experiment = mlflow.set_experiment(EXPERIMENT_NAME)

with mlflow.start_run(experiment_id=experiment.experiment_id, run_name=RUN_NAME) as run:
    mlflow.set_tag("purpose", "infrastructure-smoke-test")
    mlflow.set_tag("scope", "setup-only-no-training")
    mlflow.log_param("tracking_uri", TRACKING_URI)
    mlflow.log_text(
        "MLflow artifact proxy to local S3-compatible storage: OK\n",
        ARTIFACT_PATH,
    )

    print(f"MLFLOW_SMOKE_EXPERIMENT_ID={experiment.experiment_id}")
    print(f"MLFLOW_SMOKE_RUN_ID={run.info.run_id}")
    print(f"MLFLOW_SMOKE_ARTIFACT={ARTIFACT_PATH}")
