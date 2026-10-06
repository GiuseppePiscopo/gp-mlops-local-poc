import os

import boto3

# Connect to the S3-compatible artifact store using MLflow container variables.
client = boto3.client(
    "s3",
    endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"],
)

# Read the MLflow metadata file for the resolved model artifact V1.
response = client.get_object(
    Bucket="mlflow-artifacts",
    Key="2/models/m-8f5207a811bf4aa281ed8edc237319e0/artifacts/MLmodel",
)

# Print the metadata without exposing any credential.
print(response["Body"].read().decode("utf-8"))
