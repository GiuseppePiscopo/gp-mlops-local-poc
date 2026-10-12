#!/usr/bin/env bash

set -euo pipefail

# Define the default WSL runtime destination.
DEFAULT_TARGET_SERVING="/opt/mlops-local-poc/serving"

# Resolve the source serving directory from this script location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_SERVING="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Initialize configurable source and target directories.
SOURCE_SERVING="${DEFAULT_SOURCE_SERVING}"
TARGET_SERVING="${DEFAULT_TARGET_SERVING}"

# Print command usage and the explicit synchronization whitelist.
usage() {
  cat <<'USAGE'
Synchronize static serving sources from the authoritative Git working copy
into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-serving PATH
  --target-serving PATH
  --help

Only these static directories are synchronized:
  - config/
  - manifests/
  - scripts/
  - src/

The runtime/ directory is deliberately never copied, deleted or modified.
USAGE
}

# Parse optional source and target path overrides.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-serving)
      SOURCE_SERVING="$2"
      shift 2
      ;;
    --target-serving)
      TARGET_SERVING="$2"
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

# Convert source and target paths to absolute canonical paths.
SOURCE_SERVING="$(cd -- "${SOURCE_SERVING}" && pwd)"
TARGET_SERVING="$(mkdir -p -- "${TARGET_SERVING}" && cd -- "${TARGET_SERVING}" && pwd)"

# Prevent an accidental synchronization of a directory onto itself.
if [[ "${SOURCE_SERVING}" == "${TARGET_SERVING}" ]]; then
  echo "Source and target serving directories must be different." >&2
  exit 2
fi

# Declare source directories that must be mirrored exactly.
required_directories=(
  "config"
  "manifests"
  "scripts"
  "src"
)

# Declare root-level static files that must be copied individually.
required_files=(
  "Dockerfile"
  "requirements.txt"
)

# Validate that every required source directory exists.
for directory in "${required_directories[@]}"; do
  if [[ ! -d "${SOURCE_SERVING}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_SERVING}/${directory}" >&2
    exit 1
  fi
done

# Validate that every required root-level source file exists.
for file in "${required_files[@]}"; do
  if [[ ! -f "${SOURCE_SERVING}/${file}" ]]; then
    echo "Required source file not found: ${SOURCE_SERVING}/${file}" >&2
    exit 1
  fi
done

echo "Synchronizing static serving sources"
echo "  Source: ${SOURCE_SERVING}"
echo "  Target: ${TARGET_SERVING}"
echo

# Mirror static directories and remove obsolete synchronized files.
for directory in "${required_directories[@]}"; do
  rsync -a --delete \
    "${SOURCE_SERVING}/${directory}/" \
    "${TARGET_SERVING}/${directory}/"
done

# Copy only the explicit root-level static files.
for file in "${required_files[@]}"; do
  rsync -a \
    "${SOURCE_SERVING}/${file}" \
    "${TARGET_SERVING}/${file}"
done

# Normalize root-level static text files to Unix LF line endings.
for file in "${required_files[@]}"; do
  sed -i 's/\r$//' "${TARGET_SERVING}/${file}"
done

# Normalize supported static text files to Unix LF line endings.
while IFS= read -r -d '' file; do
  sed -i 's/\r$//' "${file}"
done < <(
  find \
    "${TARGET_SERVING}/config" \
    "${TARGET_SERVING}/manifests" \
    "${TARGET_SERVING}/scripts" \
    "${TARGET_SERVING}/src" \
    -type f \
    \( \
      -name '*.sh' -o \
      -name '*.py' -o \
      -name '*.env' -o \
      -name '*.yaml' -o \
      -name '*.yml' -o \
      -name '*.json' -o \
      -name '*.txt' \
    \) \
    -print0
)

# Set standard non-executable permissions on root-level static files.
for file in "${required_files[@]}"; do
  chmod 644 "${TARGET_SERVING}/${file}"
done

# Set standard Unix permissions on synchronized directories.
find \
  "${TARGET_SERVING}/config" \
  "${TARGET_SERVING}/manifests" \
  "${TARGET_SERVING}/scripts" \
  -type d \
  -exec chmod 755 {} +

# Set standard non-executable permissions on ordinary static files.
find \
  "${TARGET_SERVING}/config" \
  "${TARGET_SERVING}/manifests" \
  "${TARGET_SERVING}/scripts" \
  -type f \
  -exec chmod 644 {} +

# Mark only host-side Python and shell scripts as executable in WSL.
find "${TARGET_SERVING}/scripts" \
  -type f \
  \( -name '*.sh' -o -name '*.py' \) \
  -exec chmod 755 {} +

# Report the completed synchronization without modifying runtime artifacts.
echo "Synchronization completed."
echo "Source: ${SOURCE_SERVING}"
echo "Target: ${TARGET_SERVING}"
echo "The runtime/ directory was not modified."
