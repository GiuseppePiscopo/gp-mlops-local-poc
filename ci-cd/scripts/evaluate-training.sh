#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: bash evaluate-training.sh TRAINING_RESULT SNAPSHOT TEST_SET_DIRECTORY REPORT_DIRECTORY" >&2
  exit 2
fi

: "${GIT_REPOSITORY:?GIT_REPOSITORY is required}"
: "${DOCKER_NETWORK:?DOCKER_NETWORK is required}"
: "${MLFLOW_TRACKING_URI:?MLFLOW_TRACKING_URI is required}"

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

TRAINING_RESULT="$(realpath -e -- "$1")"
SNAPSHOT="$(realpath -e -- "$2")"
TEST_SET_DIRECTORY="$(realpath -e -- "$3")"
REPORT_DIRECTORY="$(realpath -m -- "$4")"
REPOSITORY="$(realpath -e -- "$GIT_REPOSITORY")"
case "$REPORT_DIRECTORY/" in
  "$REPOSITORY/"*) echo "Evaluation report must be outside the Git checkout." >&2; exit 1 ;;
esac
IMAGE="$(python3 "$REPOSITORY/ci-cd/scripts/prepare_image.py" resolve \
  --repository "$REPOSITORY" --component training --print-id)"
COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"

mkdir -m 700 -- "$REPORT_DIRECTORY"
exec docker run --rm \
  --network "$DOCKER_NETWORK" \
  --user "$(id -u):$(id -g)" \
  --env "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" \
  --mount "type=bind,src=$TRAINING_RESULT,dst=/input/training.json,readonly" \
  --mount "type=bind,src=$SNAPSHOT,dst=/input/snapshot.json,readonly" \
  --mount "type=bind,src=$TEST_SET_DIRECTORY,dst=/input/test-set,readonly" \
  --mount "type=bind,src=$REPORT_DIRECTORY,dst=/output" \
  --entrypoint python \
  "$IMAGE" -m evaluation.evaluate \
  --training-result /input/training.json \
  --input-snapshot /input/snapshot.json \
  --test-set-directory /input/test-set \
  --report /output/report.json \
  --expected-commit "$COMMIT" \
  "${MODE_ARGS[@]}"
