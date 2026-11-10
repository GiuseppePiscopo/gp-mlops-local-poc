#!/usr/bin/env python3
"""Verify a training build result against an expected commit."""

import argparse
import json
import sys
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def verify_image_provenance(build_result: Path, expected_commit: str, summary: Path | None) -> None:
    result = json.loads(build_result.read_text(encoding="utf-8"))
    expected_image = f"gp-mof-capstone-training:git-{expected_commit}"
    if result["git_commit"] != expected_commit or result["image"] != expected_image:
        raise ValueError("Built image does not match the expected commit.")

    report = (
        "## Linux checkout and image\n\n"
        f"- Commit: `{expected_commit}`\n"
        f"- Image: `{result['image']}`\n"
        f"- Image ID: `{result['image_id']}`\n"
        f"- Reused: `{result['reused']}`\n"
    )
    if summary is not None:
        with summary.open("a", encoding="utf-8") as stream:
            stream.write(report)
    print(json.dumps(result, indent=2))


def main() -> int:
    arguments = parse_arguments()
    try:
        verify_image_provenance(arguments.build_result, arguments.expected_commit, arguments.summary)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Image provenance verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
