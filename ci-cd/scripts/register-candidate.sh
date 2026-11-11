#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: bash register-candidate.sh TRAINING_RESULT SNAPSHOT EVALUATION OUTPUT_DIRECTORY" >&2
  exit 2
fi

: "${GIT_REPOSITORY:?GIT_REPOSITORY is required}"
: "${DOCKER_NETWORK:?DOCKER_NETWORK is required}"
: "${MLFLOW_TRACKING_URI:?MLFLOW_TRACKING_URI is required}"
: "${REGISTERED_MODEL_NAME:?REGISTERED_MODEL_NAME is required}"
: "${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
: "${GITHUB_REF:?GITHUB_REF is required}"

TRAINING_RESULT="$(realpath -e -- "$1")"
SNAPSHOT="$(realpath -e -- "$2")"
EVALUATION="$(realpath -e -- "$3")"
OUTPUT_DIRECTORY="$(realpath -m -- "$4")"
REPOSITORY="$(realpath -e -- "$GIT_REPOSITORY")"
case "$OUTPUT_DIRECTORY/" in
  "$REPOSITORY/"*) echo "Registration result must be outside the Git checkout." >&2; exit 1 ;;
esac
IMAGE="$(python3 "$REPOSITORY/ci-cd/scripts/prepare_image.py" resolve \
  --repository "$REPOSITORY" --component training --print-id)"
COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"

mkdir -m 700 -- "$OUTPUT_DIRECTORY"
exec docker run --rm \
  --network "$DOCKER_NETWORK" \
  --user "$(id -u):$(id -g)" \
  --env "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" \
  --mount "type=bind,src=$TRAINING_RESULT,dst=/input/training.json,readonly" \
  --mount "type=bind,src=$SNAPSHOT,dst=/input/snapshot.json,readonly" \
  --mount "type=bind,src=$EVALUATION,dst=/input/evaluation.json,readonly" \
  --mount "type=bind,src=$OUTPUT_DIRECTORY,dst=/output" \
  --entrypoint python \
  "$IMAGE" -m registration.register_candidate \
  --training-result /input/training.json \
  --input-snapshot /input/snapshot.json \
  --evaluation /input/evaluation.json \
  --registered-model "$REGISTERED_MODEL_NAME" \
  --expected-commit "$COMMIT" \
  --event-name "$GITHUB_EVENT_NAME" \
  --git-ref "$GITHUB_REF" \
  --result /output/registration-result.json
