#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash verify-checkout.sh REPOSITORY EXPECTED_COMMIT" >&2
  exit 2
fi

REPOSITORY="$1"
EXPECTED_COMMIT="$2"

if [[ "$(uname -s)" != Linux ]]; then
  echo "Checkout verification requires Linux." >&2
  exit 1
fi

FILESYSTEM="$(findmnt --noheadings --output FSTYPE --target "$REPOSITORY")"
case "$FILESYSTEM" in
  ext4|xfs|btrfs) ;;
  *) echo "Expected a Linux filesystem; found $FILESYSTEM" >&2; exit 1 ;;
esac

cd -- "$REPOSITORY"
if [[ "$(git rev-parse --show-toplevel)" != "$REPOSITORY" ]]; then
  echo "Repository must identify the Git working tree root." >&2
  exit 1
fi
if [[ "$(git rev-parse HEAD)" != "$EXPECTED_COMMIT" ]]; then
  echo "Checkout commit does not match the expected commit." >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "The runner checkout must be clean." >&2
  exit 1
fi

git lfs fsck
printf 'Checkout: %s\nFilesystem: %s\nCommit: %s\n' \
  "$REPOSITORY" "$FILESYSTEM" "$EXPECTED_COMMIT"
