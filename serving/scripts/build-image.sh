#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVING_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

REPOSITORY="${GIT_REPOSITORY:-$(git -C "${SERVING_DIR}" rev-parse --show-toplevel)}"
IMAGE_TAG="${IMAGE_TAG:-}"

exec python3 "${REPOSITORY}/ci-cd/scripts/prepare_image.py" build \
  --repository "${REPOSITORY}" --component serving --image "${IMAGE_TAG}"
