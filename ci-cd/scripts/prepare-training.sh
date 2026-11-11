#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash prepare-training.sh DATASET OUTPUT_DIRECTORY" >&2
  exit 2
fi

: "${GIT_REPOSITORY:?GIT_REPOSITORY is required}"
: "${DOCKER_NETWORK:?DOCKER_NETWORK is required}"
: "${MLFLOW_TRACKING_URI:?MLFLOW_TRACKING_URI is required}"
: "${REGISTERED_MODEL_NAME:?REGISTERED_MODEL_NAME is required}"

MODE_ARGS=()
case "${PIPELINE_BOOTSTRAP:-false}" in
  true)
    if [[ "${GITHUB_EVENT_NAME:-}" != "workflow_dispatch" ]]; then
      echo "Bootstrap is allowed only for workflow_dispatch." >&2; exit 1
    fi
    MODE_ARGS+=(--bootstrap)
    ;;
  false) ;;
  *) echo "PIPELINE_BOOTSTRAP must be true or false." >&2; exit 1 ;;
esac

DATASET="$(realpath -e -- "$1")"
OUTPUT_DIRECTORY="$(realpath -m -- "$2")"
REPOSITORY="$(realpath -e -- "$GIT_REPOSITORY")"
case "$OUTPUT_DIRECTORY/" in
  "$REPOSITORY/"*) echo "Snapshot must be outside the Git checkout." >&2; exit 1 ;;
esac
IMAGE="$(python3 "$REPOSITORY/ci-cd/scripts/prepare_image.py" resolve \
  --repository "$REPOSITORY" --component training --print-id)"
COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"

mkdir -m 700 -- "$OUTPUT_DIRECTORY"
exec docker run --rm \
  --network "$DOCKER_NETWORK" \
  --user "$(id -u):$(id -g)" \
  --env "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" \
  --mount "type=bind,src=$DATASET,dst=/data/input/SeoulBikeData.csv,readonly" \
  --mount "type=bind,src=$OUTPUT_DIRECTORY,dst=/output" \
  --entrypoint python \
  "$IMAGE" -m random_forest.prepare_training \
  --training-data /data/input/SeoulBikeData.csv \
  --output-directory /output \
  --registered-model "$REGISTERED_MODEL_NAME" \
  --git-commit "$COMMIT" \
  "${MODE_ARGS[@]}"
