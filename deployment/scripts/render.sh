#!/usr/bin/env bash

set -euo pipefail

DEFAULT_NETWORK="mlops-local-network"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIRECTORY="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIRECTORY="${DEPLOYMENT_DIRECTORY}/rendered"

REPOSITORY="${GIT_REPOSITORY:-$(git -C "${DEPLOYMENT_DIRECTORY}" rev-parse --show-toplevel)}"
IMAGE_TAG="${DEPLOYMENT_RESOLVER_IMAGE:-}"
IMAGE="$(python3 "${REPOSITORY}/ci-cd/scripts/prepare_image.py" resolve \
  --repository "${REPOSITORY}" --component deployment \
  --image "${IMAGE_TAG}" --print-id)"
NETWORK="${DEPLOYMENT_RESOLVER_NETWORK:-${DEFAULT_NETWORK}}"

mkdir -p -- "${OUTPUT_DIRECTORY}"

exec docker run --rm \
  --network "${NETWORK}" \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=${OUTPUT_DIRECTORY},dst=/output" \
  "${IMAGE}" \
  "$@"
