#!/usr/bin/env bash

set -euo pipefail

DEFAULT_TARGET_PROMETHEUS="/opt/mlops-local-poc/prometheus"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_PROMETHEUS="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SOURCE_PROMETHEUS="${DEFAULT_SOURCE_PROMETHEUS}"
TARGET_PROMETHEUS="${DEFAULT_TARGET_PROMETHEUS}"

usage() {
  cat <<'USAGE'
Synchronize the Prometheus monitoring configuration from the authoritative Git
working copy into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-prometheus PATH
      Source prometheus directory in the authoritative Git working copy.
      Default: parent directory of this script.

  --target-prometheus PATH
      Target prometheus directory in the WSL runtime workspace.
      Default: /opt/mlops-local-poc/prometheus

  --help
      Show this help message.

Only these static sources are synchronized:
  - config/
  - scripts/

No runtime or rendered directory is modified.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-prometheus)
      SOURCE_PROMETHEUS="$2"
      shift 2
      ;;
    --target-prometheus)
      TARGET_PROMETHEUS="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SOURCE_PROMETHEUS="$(cd -- "${SOURCE_PROMETHEUS}" && pwd)"
TARGET_PROMETHEUS="$(mkdir -p -- "${TARGET_PROMETHEUS}" && cd -- "${TARGET_PROMETHEUS}" && pwd)"

if [[ "${SOURCE_PROMETHEUS}" == "${TARGET_PROMETHEUS}" ]]; then
  echo "Source and target prometheus directories must be different." >&2
  exit 2
fi

required_directories=(
  "config"
  "scripts"
)

for directory in "${required_directories[@]}"; do
  if [[ ! -d "${SOURCE_PROMETHEUS}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_PROMETHEUS}/${directory}" >&2
    exit 1
  fi
done

echo "Synchronizing Prometheus monitoring sources"
echo "  Source: ${SOURCE_PROMETHEUS}"
echo "  Target: ${TARGET_PROMETHEUS}"
echo

for directory in "${required_directories[@]}"; do
  rsync -a --delete \
    "${SOURCE_PROMETHEUS}/${directory}/" \
    "${TARGET_PROMETHEUS}/${directory}/"
done

while IFS= read -r -d '' file; do
  sed -i 's/\r$//' "${file}"
done < <(
  find "${TARGET_PROMETHEUS}/config" "${TARGET_PROMETHEUS}/scripts" \
    -type f \
    \( \
      -name "*.sh" -o \
      -name "*.yaml" -o \
      -name "*.yml" -o \
      -name "*.json" -o \
      -name "*.txt" \
    \) \
    -print0
)

for directory in "${required_directories[@]}"; do
  find "${TARGET_PROMETHEUS}/${directory}" -type d -exec chmod 755 {} +
  find "${TARGET_PROMETHEUS}/${directory}" -type f -exec chmod 644 {} +
done

find "${TARGET_PROMETHEUS}/scripts" \
  -type f \
  -name "*.sh" \
  -exec chmod 755 {} +

echo
echo "Synchronization completed."
echo "Line endings were normalized to LF in static text files."
echo "Static source permissions were normalized:"
echo "  directories: 755"
echo "  ordinary files: 644"
echo "  scripts/*.sh: 755"
