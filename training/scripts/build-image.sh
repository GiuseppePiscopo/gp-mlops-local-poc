#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TRAINING_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

IMAGE_TAG="${IMAGE_TAG:-gp-mof-capstone-training:dev}"

docker build \
  --pull \
  --tag "${IMAGE_TAG}" \
  "${TRAINING_DIR}"
