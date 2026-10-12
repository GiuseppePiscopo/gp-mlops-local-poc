#!/usr/bin/env bash
#
# Converte le metriche di drift JSON in formato Prometheus e le invia a Pushgateway.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DAY="${1:-$(date +%F)}"
INSTANCE="${2:-${DAY}}"
DRIFT_FILE="/tmp/drift-metrics-${DAY}.json"
REFERENCE_LOCK="${SCRIPT_DIR}/../reference/reference.lock.json"
PUSHGATEWAY_URL="${PUSHGATEWAY_URL:-http://127.0.0.1:9091}"
JOB_NAME="model_quality_drift"

if [[ ! -f "${DRIFT_FILE}" ]]; then
  echo "Drift metrics file not found: ${DRIFT_FILE}" >&2
  exit 1
fi

if [[ ! -f "${REFERENCE_LOCK}" ]]; then
  echo "Reference lock file not found: ${REFERENCE_LOCK}" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/render-drift-metrics.py" \
  --drift "${DRIFT_FILE}" \
  --reference-lock "${REFERENCE_LOCK}" \
  --instance "${INSTANCE}" \
  | curl --fail --silent --show-error \
    --data-binary @- \
    "${PUSHGATEWAY_URL}/metrics/job/${JOB_NAME}/instance/${INSTANCE}"

echo "Metriche pushate su ${PUSHGATEWAY_URL}: job=${JOB_NAME}, instance=${INSTANCE}"
