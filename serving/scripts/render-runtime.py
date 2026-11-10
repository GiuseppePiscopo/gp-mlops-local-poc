#!/usr/bin/env python3
"""Render the committed ServingRuntime with a verified local MLServer image."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE_PLACEHOLDER = "${MLSERVER_IMAGE}"
TEMPLATE_PATH = "serving/manifests/20-serving-runtime.yaml"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "runtime" / "20-serving-runtime.yaml"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-repository", type=Path, default=os.getenv("GIT_REPOSITORY"))
    parser.add_argument("--image-tag", default=os.getenv("IMAGE_TAG"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def render_runtime(repository: Path, image_tag: str | None, output: Path) -> dict[str, str]:
    # Resolve the local image through the shared provenance checks, without building.
    arguments = [
        sys.executable,
        str(repository / "ci-cd" / "scripts" / "prepare_image.py"),
        "resolve",
        "--repository",
        str(repository),
        "--component",
        "serving",
    ]
    if image_tag:
        arguments.extend(["--image", image_tag])

    # Require the image reference, local ID and source revision from prepare_image.
    identity = json.loads(subprocess.run(arguments, text=True, capture_output=True, check=True).stdout)
    if not isinstance(identity, dict) or any(
        not isinstance(identity.get(key), str) or not identity[key] for key in ("image", "image_id", "git_commit")
    ):
        raise ValueError("Incomplete image provenance.")

    # Keep generated output separate from the versioned template.
    if output.resolve() == (repository / TEMPLATE_PATH).resolve():
        raise ValueError("The output must not overwrite the versioned template.")

    # Read the template from the same commit used to build the verified image.
    template = subprocess.run(
        ["git", "show", f"{identity['git_commit']}:{TEMPLATE_PATH}"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if template.count(IMAGE_PLACEHOLDER) != 1:
        raise ValueError(f"The committed template must contain exactly one {IMAGE_PLACEHOLDER}.")

    # JSON quoting also produces a valid YAML string for the image reference.
    rendered = template.replace(IMAGE_PLACEHOLDER, json.dumps(identity["image"]))

    # Replace the output only after the complete manifest has been written.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(rendered)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)

    return {"manifest": str(output), "image": identity["image"], "image_id": identity["image_id"]}


def main() -> None:
    arguments = parse_arguments()
    if arguments.git_repository is None:
        print("Runtime rendering failed: pass --git-repository or set GIT_REPOSITORY.", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = render_runtime(arguments.git_repository.resolve(), arguments.image_tag, arguments.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(f"Runtime rendering failed: {detail}", file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps(result))


if __name__ == "__main__":
    main()
