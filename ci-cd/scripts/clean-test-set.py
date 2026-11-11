#!/usr/bin/env python3
"""Remove only a single execution's temporary test-set directory."""

import argparse
import re
import shutil
from pathlib import Path


def clean_test_set(runner_temp: Path, workspace: str) -> None:
    if not re.fullmatch(r"test-set-[0-9]+-[0-9]+", workspace):
        raise ValueError("Invalid execution test-set workspace name.")
    parent = runner_temp.resolve(strict=True)
    target = parent / workspace
    if target.is_symlink() or target.resolve().parent != parent:
        raise ValueError("Test-set workspace must be a direct, non-symlink child of runner temp.")
    if target.exists():
        shutil.rmtree(target)
    print(f"Ephemeral test-set workspace cleaned: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-temp", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    clean_test_set(args.runner_temp, args.workspace)


if __name__ == "__main__":
    main()
