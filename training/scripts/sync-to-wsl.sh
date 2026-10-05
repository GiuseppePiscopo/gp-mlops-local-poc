#!/usr/bin/env bash

set -euo pipefail


# Define the default WSL runtime destination.
DEFAULT_TARGET_TRAINING="/opt/mlops-local-poc/training"

# Resolve the source training directory from this script location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_TRAINING="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Initialize configurable source and target directories.
SOURCE_TRAINING="${DEFAULT_SOURCE_TRAINING}"
TARGET_TRAINING="${DEFAULT_TARGET_TRAINING}"


# Print command usage and the explicit synchronization whitelist.
usage() {
  cat <<'EOF'
Synchronize the static gp-mof-capstone training build context from a Git
working copy into the WSL runtime workspace.

Usage:
  sync-to-wsl.sh [options]

Options:
  --source-training PATH
      Source training directory in the authoritative Git working copy.
      Default: parent directory of this script.

  --target-training PATH
      Target training directory in the WSL runtime workspace.
      Default: /opt/mlops-local-poc/training

  --help
      Show this help message.

Only these static sources are synchronized:
  - src/
  - scripts/
  - Dockerfile
  - .dockerignore
  - .gitignore
  - requirements.txt

The target data/ directory is deliberately never copied, deleted or modified.
EOF
}


# Parse optional source and target path overrides.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-training)
      SOURCE_TRAINING="$2"
      shift 2
      ;;
    --target-training)
      TARGET_TRAINING="$2"
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
SOURCE_TRAINING="$(cd -- "${SOURCE_TRAINING}" && pwd)"
TARGET_TRAINING="$(mkdir -p -- "${TARGET_TRAINING}" && cd -- "${TARGET_TRAINING}" && pwd)"


# Prevent an accidental synchronization of a directory onto itself.
if [[ "${SOURCE_TRAINING}" == "${TARGET_TRAINING}" ]]; then
  echo "Source and target training directories must be different." >&2
  exit 2
fi


# Declare source directories that must be mirrored exactly.
required_directories=(
  "src"
  "scripts"
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
  if [[ ! -d "${SOURCE_TRAINING}/${directory}" ]]; then
    echo "Required source directory not found: ${SOURCE_TRAINING}/${directory}" >&2
    exit 1
  fi
done


# Validate that every required root-level source file exists.
for file in "${required_files[@]}"; do
  if [[ ! -f "${SOURCE_TRAINING}/${file}" ]]; then
    echo "Required source file not found: ${SOURCE_TRAINING}/${file}" >&2
    exit 1
  fi
done


echo "Synchronizing static training sources"
echo "  Source: ${SOURCE_TRAINING}"
echo "  Target: ${TARGET_TRAINING}"
echo


# Mirror Python source files and remove obsolete copied source files.
rsync -a --delete \
  "${SOURCE_TRAINING}/src/" \
  "${TARGET_TRAINING}/src/"


# Mirror host-side scripts and remove obsolete copied scripts.
rsync -a --delete \
  "${SOURCE_TRAINING}/scripts/" \
  "${TARGET_TRAINING}/scripts/"


# Copy only the explicit root-level static files.
for file in "${required_files[@]}"; do
  rsync -a \
    "${SOURCE_TRAINING}/${file}" \
    "${TARGET_TRAINING}/${file}"
done


# Normalize root-level static text files to Unix LF line endings.
for file in "${required_files[@]}"; do
  sed -i 's/\r$//' "${TARGET_TRAINING}/${file}"
done


# Normalize Python and shell source files to Unix LF line endings.
for directory in "${required_directories[@]}"; do
  while IFS= read -r -d '' file; do
    sed -i 's/\r$//' "${file}"
  done < <(
    find "${TARGET_TRAINING}/${directory}" \
      -type f \
      \( -name "*.py" -o -name "*.sh" \) \
      -print0
  )
done


# Set standard Unix permissions on synchronized directories and ordinary files.
for directory in "${required_directories[@]}"; do
  find "${TARGET_TRAINING}/${directory}" \
    -type d \
    -exec chmod 755 {} +

  find "${TARGET_TRAINING}/${directory}" \
    -type f \
    -exec chmod 644 {} +
done


# Set standard non-executable permissions on root-level static files.
for file in "${required_files[@]}"; do
  chmod 644 "${TARGET_TRAINING}/${file}"
done


# Mark only host-side Python and shell scripts as executable.
find "${TARGET_TRAINING}/scripts" \
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
echo "  ${TARGET_TRAINING}/data/"
