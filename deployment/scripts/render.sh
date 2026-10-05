#!/usr/bin/env bash

set -euo pipefail

DEFAULT_IMAGE="gp-mof-capstone-deployment-resolver:dev"
DEFAULT_NETWORK="mlops-local-network"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIRECTORY="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIRECTORY="${DEPLOYMENT_DIRECTORY}/rendered"

IMAGE="${DEPLOYMENT_RESOLVER_IMAGE:-${DEFAULT_IMAGE}}"
NETWORK="${DEPLOYMENT_RESOLVER_NETWORK:-${DEFAULT_NETWORK}}"

mkdir -p -- "${OUTPUT_DIRECTORY}"

exec docker run --rm \
  --network "${NETWORK}" \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=${OUTPUT_DIRECTORY},dst=/output" \
  "${IMAGE}" \
  "$@"
