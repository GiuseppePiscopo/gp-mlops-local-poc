#!/usr/bin/env bash
#
# Crea il Secret con le credenziali S3 per il batch di drift schedulato.
# Risolve l'IP di SeaweedFS a ogni esecuzione: rieseguire dopo ogni
# ricreazione dello stack MLflow.

set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-kind-mlops-test}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
SECRET_NAME="${SECRET_NAME:-model-quality-s3}"

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

for variable in S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_REGION; do
  if [[ -z "${!variable:-}" ]]; then
    echo "Required MLflow environment variable is empty: ${variable}" >&2
    exit 1
  fi
done

seaweedfs_ip="$(
  docker inspect \
    --format "{{with index .NetworkSettings.Networks \"${SEAWEEDFS_DOCKER_NETWORK}\"}}{{.IPAddress}}{{end}}" \
    "${SEAWEEDFS_CONTAINER_NAME}"
)"

if [[ -z "${seaweedfs_ip}" ]]; then
  echo "Could not resolve the SeaweedFS container IP." >&2
  exit 1
fi

kubectl --context "${KUBE_CONTEXT}" -n "${MONITORING_NAMESPACE}" create secret generic "${SECRET_NAME}" \
  --from-literal="AWS_ACCESS_KEY_ID=${S3_ACCESS_KEY_ID}" \
  --from-literal="AWS_SECRET_ACCESS_KEY=${S3_SECRET_ACCESS_KEY}" \
  --from-literal="AWS_DEFAULT_REGION=${S3_REGION}" \
  --from-literal="S3_ENDPOINT_URL=http://${seaweedfs_ip}:${SEAWEEDFS_S3_PORT}" \
  --dry-run=client -o yaml \
  | kubectl --context "${KUBE_CONTEXT}" apply -f -

echo "Secret ${SECRET_NAME} applicato in ${MONITORING_NAMESPACE}."
