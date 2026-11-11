#!/usr/bin/env bash

set -euo pipefail

: "${GIT_REPOSITORY:?GIT_REPOSITORY is required}"
: "${DOCKER_NETWORK:?DOCKER_NETWORK is required}"
: "${MLFLOW_TRACKING_URI:?MLFLOW_TRACKING_URI is required}"
: "${MODEL_RUN_ID:?MODEL_RUN_ID is required}"
: "${MODEL_URI:?MODEL_URI is required}"
: "${MODEL_COMMIT:?MODEL_COMMIT is required}"
: "${REGISTERED_MODEL:?REGISTERED_MODEL is required}"
: "${MODEL_VERSION:?MODEL_VERSION is required}"
: "${EVALUATION_MODE:?EVALUATION_MODE is required}"
: "${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
: "${GITHUB_REF:?GITHUB_REF is required}"

REPOSITORY="$(realpath -e -- "$GIT_REPOSITORY")"
IMAGE="$(python3 "$REPOSITORY/ci-cd/scripts/prepare_image.py" resolve \
  --repository "$REPOSITORY" --component training --print-id)"
COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"
if [[ "$MODEL_COMMIT" != "$COMMIT" ]]; then
  echo "Approved model commit does not match the deployment checkout." >&2
  exit 1
fi

exec docker run --rm \
  --network "$DOCKER_NETWORK" \
  --user "$(id -u):$(id -g)" \
  --env "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" \
  --mount "type=bind,src=$REPOSITORY/ci-cd/scripts/check-deployment.py,dst=/app/check-deployment.py,readonly" \
  --entrypoint python \
  "$IMAGE" /app/check-deployment.py \
  --run-id "$MODEL_RUN_ID" \
  --model-uri "$MODEL_URI" \
  --expected-commit "$COMMIT" \
  --registered-model "$REGISTERED_MODEL" \
  --model-version "$MODEL_VERSION" \
  --evaluation-mode "$EVALUATION_MODE" \
  --event-name "$GITHUB_EVENT_NAME" \
  --git-ref "$GITHUB_REF"
