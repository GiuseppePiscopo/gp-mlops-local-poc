#!/usr/bin/env bash

set -euo pipefail

# Define the default WSL runtime destination.
DEFAULT_TARGET_DEPLOYMENT="/opt/mlops-local-poc/deployment"

# Resolve the source deployment directory from this script location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_DEPLOYMENT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Initialize configurable source and target directories.
SOURCE_DEPLOYMENT="${DEFAULT_SOURCE_DEPLOYMENT}"
TARGET_DEPLOYMENT="${DEFAULT_TARGET_DEPLOYMENT}"

# Print command usage and the explicit synchronization whitelist.
usage() {
  cat <<'USAGE'
Synchronize the static deployment-resolver sources from the authoritative Git
working copy into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-deployment PATH
      Source deployment directory in the authoritative Git working copy.
      Default: parent directory of this script.

  --target-deployment PATH
      Target deployment directory in the WSL runtime workspace.
      Default: /opt/mlops-local-poc/deployment

  --help
      Show this help message.

Only these static sources are synchronized:
  - src/
  - templates/
  - config/
  - scripts/
  - tests/
  - Dockerfile
  - .dockerignore
  - .gitignore
  - requirements.txt

The target rendered/ directory is deliberately never copied, deleted or modified.
USAGE
}

# Parse optional source and target path overrides.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-deployment)
      SOURCE_DEPLOYMENT="$2"
      shift 2
      ;;
    --target-deployment)
      TARGET_DEPLOYMENT="$2"
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
SOURCE_DEPLOYMENT="$(cd -- "${SOURCE_DEPLOYMENT}" && pwd)"
TARGET_DEPLOYMENT="$(mkdir -p -- "${TARGET_DEPLOYMENT}" && cd -- "${TARGET_DEPLOYMENT}" && pwd)"

# Prevent an accidental synchronization of a directory onto itself.
if [[ "${SOURCE_DEPLOYMENT}" == "${TARGET_DEPLOYMENT}" ]]; then
  echo "Source and target deployment directories must be different." >&2
  exit 2
fi

# Declare source directories that must be mirrored exactly.
required_directories=(
  "src"
  "templates"
  "config"
  "scripts"
  "tests"
)

# Declare root-level static files that must be copied individually.
required_files=(
  "Dockerfile"
  ".dockerignore"
  ".gitignore"
  "requirements.txt"
)

# Validate that every required source directory exists.
for directory in "${required_directories[@]}"; do
  if [[ ! -d "${SOURCE_DEPLOYMENT}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_DEPLOYMENT}/${directory}" >&2
    exit 1
  fi
done

# Validate that every required root-level source file exists.
for file in "${required_files[@]}"; do
  if [[ ! -f "${SOURCE_DEPLOYMENT}/${file}" ]]; then
    echo "Required source file not found: ${SOURCE_DEPLOYMENT}/${file}" >&2
    exit 1
  fi
done

echo "Synchronizing static deployment sources"
echo "  Source: ${SOURCE_DEPLOYMENT}"
echo "  Target: ${TARGET_DEPLOYMENT}"
echo

# Mirror each required source directory and remove obsolete copied files.
for directory in "${required_directories[@]}"; do
  rsync -a --delete \
    "${SOURCE_DEPLOYMENT}/${directory}/" \
    "${TARGET_DEPLOYMENT}/${directory}/"
done

# Copy only the explicit root-level static files.
for file in "${required_files[@]}"; do
  rsync -a \
    "${SOURCE_DEPLOYMENT}/${file}" \
    "${TARGET_DEPLOYMENT}/${file}"
done

# Normalize root-level static text files to Unix LF line endings.
for file in "${required_files[@]}"; do
  sed -i 's/\r$//' "${TARGET_DEPLOYMENT}/${file}"
done

# Normalize supported source and configuration files to Unix LF line endings.
while IFS= read -r -d '' file; do
  sed -i 's/\r$//' "${file}"
done < <(
  find \
    "${TARGET_DEPLOYMENT}/src" \
    "${TARGET_DEPLOYMENT}/templates" \
    "${TARGET_DEPLOYMENT}/config" \
    "${TARGET_DEPLOYMENT}/scripts" \
    "${TARGET_DEPLOYMENT}/tests" \
    -type f \
    \( \
      -name "*.py" -o \
      -name "*.sh" -o \
      -name "*.yaml" -o \
      -name "*.yml" -o \
      -name "*.j2" -o \
      -name "*.json" -o \
      -name "*.toml" -o \
      -name "*.txt" \
    \) \
    -print0
)

# Set standard Unix permissions on synchronized directories and ordinary files.
for directory in "${required_directories[@]}"; do
  find "${TARGET_DEPLOYMENT}/${directory}" -type d -exec chmod 755 {} +
  find "${TARGET_DEPLOYMENT}/${directory}" -type f -exec chmod 644 {} +
done

# Set standard non-executable permissions on root-level static files.
for file in "${required_files[@]}"; do
  chmod 644 "${TARGET_DEPLOYMENT}/${file}"
done

# Mark only host-side Python and shell scripts as executable.
find "${TARGET_DEPLOYMENT}/scripts" \
  -type f \
  \( -name "*.sh" -o -name "*.py" \) \
  -exec chmod 755 {} +

echo
echo "Synchronization completed."
echo "Line endings were normalized to LF in static text files."
echo "Static source permissions were normalized:"
echo "  directories: 755"
echo "  ordinary files: 644"
echo "  scripts/*.sh and scripts/*.py: 755"
echo
echo "The following runtime directory was not modified:"
echo "  ${TARGET_DEPLOYMENT}/rendered/"
