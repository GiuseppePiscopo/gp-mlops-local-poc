#!/usr/bin/env bash
#
# Esporta i record JSONL di inferenza dai log del predictor su SeaweedFS S3,
# partizionati per data di esecuzione: s3://<bucket>/inference/date=YYYY-MM-DD/.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/export.env"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Configuration file not found: ${CONFIG_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${CONFIG_FILE}"

# Load local SeaweedFS credentials without printing them.
set -a
# shellcheck disable=SC1090
source "${MLFLOW_ENV_FILE}"
set +a

# Resolve the SeaweedFS IP on every run because it can change.
seaweedfs_ip="$(
  docker inspect \
    --format "{{with index .NetworkSettings.Networks \"${SEAWEEDFS_DOCKER_NETWORK}\"}}{{.IPAddress}}{{end}}" \
    "${SEAWEEDFS_CONTAINER_NAME}"
)"

if [[ -z "${seaweedfs_ip}" ]]; then
  echo "Could not resolve the SeaweedFS container IP." >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

DAY="$(date +%F)"
FILE_NAME="inference-${DAY}.jsonl"
OUT_FILE="${TMPDIR}/${FILE_NAME}"

POD="$(kubectl --context "${KUBE_CONTEXT}" -n "${KUBERNETES_NAMESPACE}" \
  get pods -l "${POD_LABEL}" \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')"

echo "Pod: ${POD}"

kubectl --context "${KUBE_CONTEXT}" -n "${KUBERNETES_NAMESPACE}" \
  logs "${POD}" -c kserve-container --since="${LOG_SINCE}" \
  | python3 "${SCRIPT_DIR}/filter-log-records.py" "${OUT_FILE}"

# Include i log del container precedente solo se Kubernetes ne conserva uno.
if kubectl --context "${KUBE_CONTEXT}" -n "${KUBERNETES_NAMESPACE}" \
  logs "${POD}" -c kserve-container --since="${LOG_SINCE}" -p >/dev/null 2>&1; then
  kubectl --context "${KUBE_CONTEXT}" -n "${KUBERNETES_NAMESPACE}" \
    logs "${POD}" -c kserve-container --since="${LOG_SINCE}" -p \
    | python3 "${SCRIPT_DIR}/filter-log-records.py" "${OUT_FILE}.prev"
  cat "${OUT_FILE}.prev" >> "${OUT_FILE}"
  rm -f "${OUT_FILE}.prev"
else
  echo "Nessun container precedente disponibile per ${POD}; salto i log precedenti."
fi

if [[ ! -s "${OUT_FILE}" ]]; then
  echo "Nessun record da esportare." >&2
  exit 1
fi

docker image inspect "${AWS_CLI_IMAGE}" >/dev/null 2>&1 \
  || docker pull "${AWS_CLI_IMAGE}"

S3_KEY="s3://${LOG_BUCKET}/${LOG_PREFIX}/date=${DAY}/${FILE_NAME}"

# Carica il file del giorno su S3.
docker run --rm \
  -v "${TMPDIR}:/data:ro" \
  --network "${SEAWEEDFS_DOCKER_NETWORK}" \
  -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
  -e AWS_DEFAULT_REGION="${S3_REGION}" \
  "${AWS_CLI_IMAGE}" \
  --endpoint-url "http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  s3 cp "/data/${FILE_NAME}" "${S3_KEY}"

# Elenca il contenuto della partizione per conferma.
docker run --rm \
  --network "${SEAWEEDFS_DOCKER_NETWORK}" \
  -e AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" \
  -e AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
  -e AWS_DEFAULT_REGION="${S3_REGION}" \
  "${AWS_CLI_IMAGE}" \
  --endpoint-url "http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  s3 ls "${S3_KEY%/*}/"

echo "Export completato: ${S3_KEY}"
