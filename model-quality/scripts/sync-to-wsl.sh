#!/usr/bin/env bash

set -euo pipefail

DEFAULT_TARGET_COMPONENT="/opt/mlops-local-poc/model-quality"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_COMPONENT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SOURCE_COMPONENT="${DEFAULT_SOURCE_COMPONENT}"
TARGET_COMPONENT="${DEFAULT_TARGET_COMPONENT}"

usage() {
  cat <<'USAGE'
Synchronize the model quality sources from the authoritative Git
working copy into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-component PATH
      Source model-quality directory in the authoritative Git working copy.
      Default: parent directory of this script.

  --target-component PATH
      Target model-quality directory in the WSL runtime workspace.
      Default: /opt/mlops-local-poc/model-quality

  --help
      Show this help message.

Only these static sources are synchronized:
  - config/
  - dashboards/
  - scripts/

No runtime or rendered directory is modified.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-component)
      SOURCE_COMPONENT="$2"
      shift 2
      ;;
    --target-component)
      TARGET_COMPONENT="$2"
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

SOURCE_COMPONENT="$(cd -- "${SOURCE_COMPONENT}" && pwd)"
TARGET_COMPONENT="$(mkdir -p -- "${TARGET_COMPONENT}" && cd -- "${TARGET_COMPONENT}" && pwd)"

if [[ "${SOURCE_COMPONENT}" == "${TARGET_COMPONENT}" ]]; then
  echo "Source and target component directories must be different." >&2
  exit 2
fi

required_directories=(
  "config"
  "dashboards"
  "scripts"
)

# Declare root-level static files that must be copied individually.
required_files=(
  "Dockerfile"
  "requirements.txt"
)

for directory in "${required_directories[@]}"; do
  if [[ ! -d "${SOURCE_COMPONENT}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_COMPONENT}/${directory}" >&2
    exit 1
  fi
done

# Validate that every required root-level source file exists.
for file in "${required_files[@]}"; do
  if [[ ! -f "${SOURCE_COMPONENT}/${file}" ]]; then
    echo "Required source file not found: ${SOURCE_COMPONENT}/${file}" >&2
    exit 1
  fi
done

echo "Synchronizing model quality sources"
echo "  Source: ${SOURCE_COMPONENT}"
echo "  Target: ${TARGET_COMPONENT}"
echo

for directory in "${required_directories[@]}"; do
  rsync -a --delete \
    "${SOURCE_COMPONENT}/${directory}/" \
    "${TARGET_COMPONENT}/${directory}/"
done

# Copy only the explicit root-level static files.
for file in "${required_files[@]}"; do
  rsync -a \
    "${SOURCE_COMPONENT}/${file}" \
    "${TARGET_COMPONENT}/${file}"
done

# Normalize root-level static text files to Unix LF line endings.
for file in "${required_files[@]}"; do
  sed -i 's/\r$//' "${TARGET_COMPONENT}/${file}"
done

while IFS= read -r -d '' file; do
  sed -i 's/\r$//' "${file}"
done < <(
  find "${TARGET_COMPONENT}/config" "${TARGET_COMPONENT}/dashboards" "${TARGET_COMPONENT}/scripts" \
    -type f \
    \( \
      -name "*.sh" -o \
      -name "*.yaml" -o \
      -name "*.yml" -o \
      -name "*.json" -o \
      -name "*.txt" -o \
      -name "*.env" \
    \) \
    -print0
)

for directory in "${required_directories[@]}"; do
  find "${TARGET_COMPONENT}/${directory}" -type d -exec chmod 755 {} +
  find "${TARGET_COMPONENT}/${directory}" -type f -exec chmod 644 {} +
done

find "${TARGET_COMPONENT}/scripts" \
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
