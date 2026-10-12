#!/usr/bin/env bash
#
# Verifica i log strutturati di inferenza nel pod predictor:
# estrae le righe JSONL e ne valida lo schema minimo.

set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-kind-mlops-test}"
NAMESPACE="${NAMESPACE:-gp-mof-capstone-serving}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

POD="$(kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" \
  get pods -l app=isvc.gp-mof-capstone-model-predictor \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')"

echo "Pod: ${POD}"

kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" \
  logs "${POD}" -c kserve-container --tail=200 \
  | python3 "${SCRIPT_DIR}/verify-log-records.py"
