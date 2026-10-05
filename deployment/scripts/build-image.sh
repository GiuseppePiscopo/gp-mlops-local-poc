#!/usr/bin/env bash

set -euo pipefail

DEFAULT_IMAGE="gp-mof-capstone-deployment-resolver:dev"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIRECTORY="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${DEPLOYMENT_RESOLVER_IMAGE:-${DEFAULT_IMAGE}}"

exec docker build \
  --tag "${IMAGE}" \
  "${DEPLOYMENT_DIRECTORY}"