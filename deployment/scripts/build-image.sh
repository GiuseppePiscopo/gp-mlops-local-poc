#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIRECTORY="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

REPOSITORY="${GIT_REPOSITORY:-$(git -C "${DEPLOYMENT_DIRECTORY}" rev-parse --show-toplevel)}"
IMAGE_TAG="${DEPLOYMENT_RESOLVER_IMAGE:-}"

exec python3 "${REPOSITORY}/ci-cd/scripts/prepare_image.py" build \
  --repository "${REPOSITORY}" --component deployment --image "${IMAGE_TAG}"