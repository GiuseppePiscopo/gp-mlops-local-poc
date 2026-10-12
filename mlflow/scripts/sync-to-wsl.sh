#!/usr/bin/env bash

set -euo pipefail

# Define the default WSL runtime destination.
DEFAULT_TARGET_MLFLOW="/opt/mlops-local-poc/mlflow"

# Resolve the source mlflow directory from this script location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_MLFLOW="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Initialize configurable source and target directories.
SOURCE_MLFLOW="${DEFAULT_SOURCE_MLFLOW}"
TARGET_MLFLOW="${DEFAULT_TARGET_MLFLOW}"

# Print command usage and the explicit synchronization whitelist.
usage() {
  cat <<'USAGE'
Synchronize the static mlflow stack sources from the authoritative Git
working copy into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-mlflow PATH
      Source mlflow directory in the authoritative Git working copy.
      Default: parent directory of this script.

  --target-mlflow PATH
      Target mlflow directory in the WSL runtime workspace.
      Default: /opt/mlops-local-poc/mlflow

  --help
      Show this help message.

Only these static sources are synchronized:
  - scripts/
  - object-store/ (except object-store/s3.json, which holds local secrets)
  - compose.yaml
  - .env.example
  - .gitignore

The runtime files .env and object-store/s3.json and the whole data/
directory (SeaweedFS files, postgres leftovers, pg_dump backups) are
deliberately never copied, deleted or modified.
USAGE
}

# Parse optional source and target path overrides.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-mlflow)
      SOURCE_MLFLOW="$2"
      shift 2
      ;;
    --target-mlflow)
      TARGET_MLFLOW="$2"
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
SOURCE_MLFLOW="$(cd -- "${SOURCE_MLFLOW}" && pwd)"
TARGET_MLFLOW="$(mkdir -p -- "${TARGET_MLFLOW}" && cd -- "${TARGET_MLFLOW}" && pwd)"

# Prevent an accidental synchronization of a directory onto itself.
if [[ "${SOURCE_MLFLOW}" == "${TARGET_MLFLOW}" ]]; then
  echo "Source and target mlflow directories must be different." >&2
  exit 2
fi

# Declare source directories that must be mirrored exactly.
required_directories=(
  "scripts"
  "object-store"
)

# Declare root-level static files that must be copied individually.
required_files=(
  "compose.yaml"
  ".env.example"
  ".gitignore"
)

# Declare per-directory rsync excludes for runtime secrets.
declare -A directory_excludes=(
  ["object-store"]="s3.json"
)

# Validate that every required source directory exists.
for directory in "${required_directories[@]}"; do
  if [[ ! -d "${SOURCE_MLFLOW}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_MLFLOW}/${directory}" >&2
    exit 1
  fi
done

# Validate that every required root-level source file exists.
for file in "${required_files[@]}"; do
  if [[ ! -f "${SOURCE_MLFLOW}/${file}" ]]; then
    echo "Required source file not found: ${SOURCE_MLFLOW}/${file}" >&2
    exit 1
  fi
done

echo "Synchronizing static mlflow sources"
echo "  Source: ${SOURCE_MLFLOW}"
echo "  Target: ${TARGET_MLFLOW}"
echo

# Mirror each required source directory and remove obsolete copied files,
# keeping runtime secrets out of the synchronization.
for directory in "${required_directories[@]}"; do
  if [[ -n "${directory_excludes[${directory}]:-}" ]]; then
    rsync -a --delete \
      --exclude="${directory_excludes[${directory}]}" \
      "${SOURCE_MLFLOW}/${directory}/" \
      "${TARGET_MLFLOW}/${directory}/"
  else
    rsync -a --delete \
      "${SOURCE_MLFLOW}/${directory}/" \
      "${TARGET_MLFLOW}/${directory}/"
  fi
done

# Copy only the explicit root-level static files.
for file in "${required_files[@]}"; do
  rsync -a \
    "${SOURCE_MLFLOW}/${file}" \
    "${TARGET_MLFLOW}/${file}"
done

# Normalize root-level static text files to Unix LF line endings.
for file in "${required_files[@]}"; do
  sed -i 's/\r$//' "${TARGET_MLFLOW}/${file}"
done

# Normalize supported source and template files to Unix LF line endings.
# Runtime secrets (e.g. object-store/s3.json) are always skipped.
while IFS= read -r -d '' file; do
  sed -i 's/\r$//' "${file}"
done < <(
  find \
    "${TARGET_MLFLOW}/scripts" \
    "${TARGET_MLFLOW}/object-store" \
    -type f \
    ! -name 's3.json' \
    \( \
      -name "*.py" -o \
      -name "*.sh" -o \
      -name "*.yaml" -o \
      -name "*.yml" -o \
      -name "*.json" -o \
      -name "*.example" -o \
      -name "*.txt" \
    \) \
    -print0
)

# Set standard Unix permissions on synchronized directories and ordinary files,
# always skipping runtime secrets excluded from the synchronization above.
for directory in "${required_directories[@]}"; do
  find "${TARGET_MLFLOW}/${directory}" \
    -type d \
    -exec chmod 755 {} +

  if [[ -n "${directory_excludes[${directory}]:-}" ]]; then
    find "${TARGET_MLFLOW}/${directory}" \
      -type f \
      ! -name "${directory_excludes[${directory}]}" \
      -exec chmod 644 {} +
  else
    find "${TARGET_MLFLOW}/${directory}" \
      -type f \
      -exec chmod 644 {} +
  fi
done

# Set standard non-executable permissions on root-level static files.
for file in "${required_files[@]}"; do
  chmod 644 "${TARGET_MLFLOW}/${file}"
done

# Mark only host-side Python and shell scripts as executable.
find "${TARGET_MLFLOW}/scripts" \
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
echo "The following runtime files were not modified:"
echo "  ${TARGET_MLFLOW}/.env"
echo "  ${TARGET_MLFLOW}/object-store/s3.json"
echo "  ${TARGET_MLFLOW}/data/"
