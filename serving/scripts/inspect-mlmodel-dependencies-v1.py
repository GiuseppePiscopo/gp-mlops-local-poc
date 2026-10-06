import os

import boto3

# Connect to the S3-compatible artifact store using MLflow container variables.
client = boto3.client(
    "s3",
    endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"],
)

# Define dependency metadata files expected in a standard MLflow model package.
artifact_prefix = "2/models/m-8f5207a811bf4aa281ed8edc237319e0/artifacts"
filenames = ("requirements.txt", "python_env.yaml", "conda.yaml")

# Print each dependency file when it exists without exposing credentials.
for filename in filenames:
    key = f"{artifact_prefix}/{filename}"

    try:
        response = client.get_object(
            Bucket="mlflow-artifacts",
            Key=key,
        )
    except client.exceptions.NoSuchKey:
        print(f"----- {filename}: NOT_FOUND -----")
        continue

    print(f"----- {filename} -----")
    print(response["Body"].read().decode("utf-8").rstrip())
