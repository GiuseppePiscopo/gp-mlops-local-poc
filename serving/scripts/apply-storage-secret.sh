#!/usr/bin/env bash

set -euo pipefail

# Resolve the static serving configuration relative to this script.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/storage-secret.env"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Configuration file not found: ${CONFIG_FILE}" >&2
  exit 1
fi

# Load non-secret serving configuration.
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

required_variables=(
  KUBERNETES_NAMESPACE
  SERVICE_ACCOUNT_NAME
  S3_SECRET_NAME
  SEAWEEDFS_CONTAINER_NAME
  SEAWEEDFS_DOCKER_NETWORK
  SEAWEEDFS_S3_PORT
  MLFLOW_ENV_FILE
)

# Fail early when required non-secret configuration is missing.
for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "Required configuration variable is empty: ${variable}" >&2
    exit 1
  fi
done

if [[ ! -f "${MLFLOW_ENV_FILE}" ]]; then
  echo "MLflow environment file not found: ${MLFLOW_ENV_FILE}" >&2
  exit 1
fi

# Load local MLflow/SeaweedFS credentials without printing them.
set -a
# shellcheck disable=SC1090
source "${MLFLOW_ENV_FILE}"
set +a

for variable in S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_REGION; do
  if [[ -z "${!variable:-}" ]]; then
    echo "Required MLflow environment variable is empty: ${variable}" >&2
    exit 1
  fi
done

# Resolve the IP on every run because it can change when the stack is recreated.
seaweedfs_ip="$(
  docker inspect \
    --format "{{with index .NetworkSettings.Networks \"${SEAWEEDFS_DOCKER_NETWORK}\"}}{{.IPAddress}}{{end}}" \
    "${SEAWEEDFS_CONTAINER_NAME}"
)"

if [[ -z "${seaweedfs_ip}" ]]; then
  echo "Could not resolve the SeaweedFS container IP." >&2
  exit 1
fi

s3_endpoint="${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}"

# Create or update the Kubernetes Secret without a client-side apply annotation.
kubectl -n "${KUBERNETES_NAMESPACE}" create secret generic "${S3_SECRET_NAME}" \
  --from-literal="AWS_ACCESS_KEY_ID=${S3_ACCESS_KEY_ID}" \
  --from-literal="AWS_SECRET_ACCESS_KEY=${S3_SECRET_ACCESS_KEY}" \
  --dry-run=client \
  -o yaml \
  | kubectl -n "${KUBERNETES_NAMESPACE}" apply \
    --server-side \
    --force-conflicts \
    --field-manager=gp-mof-capstone-serving \
    -f -

# Configure KServe storage initializer for SeaweedFS S3 over HTTP and path style.
kubectl -n "${KUBERNETES_NAMESPACE}" annotate secret "${S3_SECRET_NAME}" \
  --overwrite \
  "serving.kserve.io/s3-endpoint=${s3_endpoint}" \
  "serving.kserve.io/s3-usehttps=0" \
  "serving.kserve.io/s3-region=${S3_REGION}" \
  "serving.kserve.io/s3-usevirtualbucket=0"

# Remove any legacy client-side apply annotation that could contain Secret data.
kubectl -n "${KUBERNETES_NAMESPACE}" annotate secret "${S3_SECRET_NAME}" \
  kubectl.kubernetes.io/last-applied-configuration- \
  2>/dev/null || true

# Report only non-sensitive deployment details.
echo "S3 storage Secret applied."
echo "  Namespace: ${KUBERNETES_NAMESPACE}"
echo "  Secret: ${S3_SECRET_NAME}"
echo "  Endpoint: ${s3_endpoint}"
echo "  ServiceAccount: ${SERVICE_ACCOUNT_NAME}"
