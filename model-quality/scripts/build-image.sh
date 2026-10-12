#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPONENT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

IMAGE_TAG="${IMAGE_TAG:-gp-mof-capstone-drift:dev}"

docker build \
  --pull \
  --tag "${IMAGE_TAG}" \
  "${COMPONENT_DIR}"
