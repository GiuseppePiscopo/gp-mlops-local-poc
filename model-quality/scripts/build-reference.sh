#!/usr/bin/env bash
#
# Costruisce il reference dataset della versione servita e lo carica su
# SeaweedFS S3: s3://mlops-logs/reference/model=v<version>/.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPONENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

MODEL="${MODEL:-gp-mof-capstone-model}"
ALIAS="${ALIAS:-champion}"
DATASET_FILE="${DATASET_FILE:-/opt/mlops-local-poc/training/data/input/SeoulBikeData.csv}"
MLFLOW_ENV_FILE="${MLFLOW_ENV_FILE:-/opt/mlops-local-poc/mlflow/.env}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/mlops-local-poc/model-quality/reference}"
MLSERVER_IMAGE="${MLSERVER_IMAGE:?Impostare MLSERVER_IMAGE con il tag MLServer presente in Docker.}"

SEAWEEDFS_CONTAINER_NAME="${SEAWEEDFS_CONTAINER_NAME:-mlops-seaweedfs}"
SEAWEEDFS_DOCKER_NETWORK="${SEAWEEDFS_DOCKER_NETWORK:-mlops-local-network}"
SEAWEEDFS_S3_PORT="${SEAWEEDFS_S3_PORT:-8333}"
LOG_BUCKET="${LOG_BUCKET:-mlops-logs}"
AWS_CLI_IMAGE="${AWS_CLI_IMAGE:-amazon/aws-cli}"

if [[ ! -f "${DATASET_FILE}" ]]; then
  echo "Dataset non trovato: ${DATASET_FILE}" >&2
  exit 1
fi

# Load local MLflow/SeaweedFS credentials without printing them.
set -a
# shellcheck disable=SC1090
source "${MLFLOW_ENV_FILE}"
set +a

seaweedfs_ip="$(
  docker inspect \
    --format "{{with index .NetworkSettings.Networks \"${SEAWEEDFS_DOCKER_NETWORK}\"}}{{.IPAddress}}{{end}}" \
    "${SEAWEEDFS_CONTAINER_NAME}"
)"

if [[ -z "${seaweedfs_ip}" ]]; then
  echo "Could not resolve the SeaweedFS container IP." >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

# Esegue il builder nell'immagine MLServer: risolve l'alias, verifica il dataset,
# ricalcola le predizioni e scrive statistiche e lock in OUTPUT_DIR.
docker run --rm \
  --network "${SEAWEEDFS_DOCKER_NETWORK}" \
  -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
  -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
  -e AWS_ENDPOINT_URL="http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  -e AWS_DEFAULT_REGION="${S3_REGION}" \
  -v "${COMPONENT_DIR}/scripts:/scripts:ro" \
  -v "${DATASET_FILE}:/data/input.csv:ro" \
  -v "${OUTPUT_DIR}:/output" \
  "${MLSERVER_IMAGE}" \
  python3 /scripts/build-reference.py \
    --model "${MODEL}" \
    --alias "${ALIAS}" \
    --dataset /data/input.csv \
    --output-dir /output

VERSION="$(python3 -c "import json; print(json.load(open('${OUTPUT_DIR}/reference.lock.json'))['resolved_version'])")"
S3_PREFIX="s3://${LOG_BUCKET}/reference/model=v${VERSION}"

# Carica statistiche e lock su S3 sotto il prefisso versionato.
docker run --rm \
  -v "${OUTPUT_DIR}:/data:ro" \
  --network "${SEAWEEDFS_DOCKER_NETWORK}" \
  -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
  -e AWS_DEFAULT_REGION="${S3_REGION}" \
  "${AWS_CLI_IMAGE}" \
  --endpoint-url "http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  s3 sync /data "${S3_PREFIX}/"

echo "Reference caricato: ${S3_PREFIX}/"
