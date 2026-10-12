#!/usr/bin/env bash
#
# Scarica il batch di inferenza della data indicata da SeaweedFS,
# calcola il drift rispetto al reference e scrive le metriche in JSON.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/export.env"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Configuration file not found: ${CONFIG_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${CONFIG_FILE}"

DAY="${1:-$(date +%F)}"
REFERENCE_DIR="${SCRIPT_DIR}/../reference"
OUTPUT_FILE="/tmp/drift-metrics-${DAY}.json"
INPUT_FILE="/tmp/current-inference-${DAY}.jsonl"

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

S3_OBJECT="s3://${LOG_BUCKET}/${LOG_PREFIX}/date=${DAY}/inference-${DAY}.jsonl"

docker run --rm \
  --network "${SEAWEEDFS_DOCKER_NETWORK}" \
  -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
  -e AWS_DEFAULT_REGION="${S3_REGION}" \
  -v /tmp:/data \
  "${AWS_CLI_IMAGE}" \
  --endpoint-url "http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  s3 cp "${S3_OBJECT}" "/data/$(basename "${INPUT_FILE}")"

DRIFT_COMMAND="
pip install --quiet numpy==1.26.4 pandas==2.2.3 &&
python /scripts/compute-drift.py \
  --input /data/$(basename "${INPUT_FILE}") \
  --reference /reference \
  --output /data/$(basename "${OUTPUT_FILE}")
"

docker run --rm \
  -v "${SCRIPT_DIR}:/scripts:ro" \
  -v "${REFERENCE_DIR}:/reference:ro" \
  -v /tmp:/data \
  python:3.11-slim \
  sh -c "${DRIFT_COMMAND}"

cat "${OUTPUT_FILE}"