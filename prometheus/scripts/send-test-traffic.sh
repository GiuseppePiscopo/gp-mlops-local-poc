#!/usr/bin/env bash
#
# Invia N richieste di inferenza V2 al modello tramite Envoy Gateway,
# per generare traffico osservabile nelle metriche Envoy e MLServer.
#
# Richiede:
# - il port-forward verso il Service del gateway su 127.0.0.1:8080;
# - il file di richiesta prodotto dalla procedura di serving.
#
# Uso:
#   send-test-traffic.sh [COUNT]
#
# Variabili d'ambiente: GATEWAY_URL, MODEL, NAMESPACE, REQUEST_FILE.

set -euo pipefail

COUNT="${1:-5}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"
MODEL="${MODEL:-gp-mof-capstone-model}"
NAMESPACE="${NAMESPACE:-gp-mof-capstone-serving}"
REQUEST_FILE="${REQUEST_FILE:-/opt/mlops-local-poc/serving/runtime/infer-request.json}"

if [[ ! -f "${REQUEST_FILE}" ]]; then
  echo "File di richiesta non trovato: ${REQUEST_FILE}" >&2
  echo "Produrlo con la procedura di serving prima di generare traffico." >&2
  exit 1
fi

HOST="${MODEL}-${NAMESPACE}.example.com"

for ((i = 1; i <= COUNT; i++)); do
  curl --fail --silent --show-error --max-time 15 \
    -H "Host: ${HOST}" \
    -H 'Content-Type: application/json' \
    --data-binary "@${REQUEST_FILE}" \
    -o /dev/null \
    -w "richiesta ${i}/${COUNT}: %{http_code}\n" \
    "${GATEWAY_URL}/v2/models/${MODEL}/infer"
done
