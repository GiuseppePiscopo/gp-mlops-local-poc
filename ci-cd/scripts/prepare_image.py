#!/usr/bin/env python3
"""Build or resolve commit-tagged local images from a clean Git snapshot."""

import argparse
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

IMAGE_NAMES = {
    "training": "gp-mof-capstone-training",
    "deployment": "gp-mof-capstone-deployment-resolver",
    "serving": "gp-mof-capstone-mlserver",
}
LABEL_PREFIX = "local.mlops."


def command(arguments: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def snapshot(repository: Path, component: str) -> tuple[str, bytes]:
    root = Path(command(["git", "rev-parse", "--show-toplevel"], cwd=repository)).resolve()
    if root != repository.resolve():
        raise ValueError("--repository must identify the Git repository root.")
    if command(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root):
        raise ValueError("The authoritative repository must be clean. Commit or isolate changes before building.")
    commit = command(["git", "rev-parse", "HEAD"], cwd=root)
    archive = subprocess.run(
        ["git", "archive", "--format=zip", commit, "--", component],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        if f"{component}/Dockerfile" not in files.namelist():
            raise ValueError(f"No committed Dockerfile for component {component}.")
    return commit, archive


def image_reference(component: str, commit: str, override: str | None) -> str:
    name = IMAGE_NAMES[component]
    reference = override or f"{name}:git-{commit}"
    if not re.fullmatch(rf"{re.escape(name)}:git-{commit}(?:-[a-zA-Z0-9_][a-zA-Z0-9_.-]*)?", reference):
        raise ValueError(f"Image must be {name}:git-{commit}, optionally with an explicit rebuild suffix.")
    if len(reference.split(":", 1)[1]) > 128:
        raise ValueError("Docker image tags must not exceed 128 characters.")
    return reference


def inspect_image(reference: str) -> dict:
    images = json.loads(command(["docker", "image", "inspect", reference]))
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ValueError(f"Unexpected Docker inspect result for {reference}.")
    return images[0]


def provenance(reference: str, expected: dict[str, str]) -> dict:
    image = inspect_image(reference)
    labels = image.get("Config", {}).get("Labels") or {}
    if any(labels.get(key) != value for key, value in expected.items()):
        raise ValueError(f"Image provenance mismatch for {reference}; refusing reuse.")
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError(f"Missing or invalid local image ID for {reference}.")
    digests = image.get("RepoDigests") or []
    if not isinstance(digests, list) or not all(isinstance(value, str) for value in digests):
        raise ValueError(f"Invalid RepoDigests for {reference}.")
    return {
        "image": reference,
        "image_id": image_id,
        "repo_digests": digests,
        "git_commit": expected["org.opencontainers.image.revision"],
        "context_sha256": expected[f"{LABEL_PREFIX}context-sha256"],
    }


def extract_snapshot(archive: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        for member in files.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError(f"Unsupported snapshot entry: {member.filename}")
            if not member.is_dir() and files.read(member).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError(f"LFS pointer in image snapshot: {member.filename}")
        files.extractall(destination)


def prepare_image(action: str, repository: Path, component: str, override: str | None) -> dict:
    commit, archive = snapshot(repository, component)
    reference = image_reference(component, commit, override)
    expected = {
        "org.opencontainers.image.revision": commit,
        f"{LABEL_PREFIX}component": component,
        f"{LABEL_PREFIX}context-sha256": hashlib.sha256(archive).hexdigest(),
    }
    if action == "resolve":
        return provenance(reference, expected)

    lock = Path(tempfile.gettempdir()) / f"mlops-image-{hashlib.sha256(reference.encode()).hexdigest()}.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError(
            f"Image operation already locked: {lock}. Check for an active build before removing a stale lock."
        ) from error
    try:
        # Listing fails explicitly when the Docker daemon is unavailable.
        existing = command(["docker", "image", "ls", "--quiet", "--no-trunc", reference])
        if existing:
            result = provenance(reference, expected)
            result["reused"] = True
            return result
        with tempfile.TemporaryDirectory(prefix="mlops-build-") as directory:
            context = Path(directory)
            extract_snapshot(archive, context)
            iid_file = context / "image-id"
            arguments = ["docker", "build", "--pull", "--iidfile", str(iid_file)]
            for key, value in expected.items():
                arguments.extend(["--label", f"{key}={value}"])
            arguments.append(str(context / component))
            subprocess.run(arguments, check=True, stdout=sys.stderr)
            image_id = iid_file.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("Docker build did not return a valid image ID.")
            if command(["docker", "image", "ls", "--quiet", "--no-trunc", reference]):
                raise ValueError(f"Tag appeared during build: {reference}; refusing overwrite.")
            command(["docker", "tag", image_id, reference])
        result = provenance(reference, expected)
        result["reused"] = False
        return result
    finally:
        lock.rmdir()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "resolve"))
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--component", choices=IMAGE_NAMES, required=True)
    parser.add_argument("--image")
    parser.add_argument("--print-id", action="store_true", help="Print only the verified image ID for docker run.")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    try:
        result = prepare_image(arguments.action, arguments.repository, arguments.component, arguments.image)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr
        else:
            detail = str(error)
        print(f"Image preparation failed: {detail}", file=sys.stderr)
        raise SystemExit(2) from error
    print(result["image_id"] if arguments.print_id else json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
