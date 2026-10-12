#!/usr/bin/env bash
#
# Applica la dashboard Grafana del model quality dal file versionato nel repository.
# La ConfigMap etichettata viene raccolta dal sidecar delle dashboard di Grafana.

set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-kind-mlops-test}"
DASHBOARD_NAMESPACE="${DASHBOARD_NAMESPACE:-monitoring}"
CONFIGMAP_NAME="${CONFIGMAP_NAME:-model-quality-drift-dashboard}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPONENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DASHBOARD_FILE="${COMPONENT_DIR}/dashboards/model-quality-drift-dashboard.json"

if [[ ! -f "${DASHBOARD_FILE}" ]]; then
  echo "Dashboard non trovata: ${DASHBOARD_FILE}" >&2
  exit 1
fi

python3 -c "import json,sys; json.load(open(sys.argv[1]))" "${DASHBOARD_FILE}"

kubectl --context "${KUBE_CONTEXT}" create configmap "${CONFIGMAP_NAME}" \
  --namespace "${DASHBOARD_NAMESPACE}" \
  --from-file="model-quality-drift-dashboard.json=${DASHBOARD_FILE}" \
  --dry-run=client -o yaml \
  | kubectl --context "${KUBE_CONTEXT}" apply -f -

kubectl --context "${KUBE_CONTEXT}" label configmap "${CONFIGMAP_NAME}" \
  --namespace "${DASHBOARD_NAMESPACE}" \
  grafana_dashboard=1 --overwrite

echo "Dashboard applicata come ConfigMap/${CONFIGMAP_NAME} in ${DASHBOARD_NAMESPACE}."
