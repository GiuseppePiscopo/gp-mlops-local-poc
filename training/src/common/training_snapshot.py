"""Create and validate per-execution dataset and champion snapshots."""

import hashlib
import json
import re
from pathlib import Path

from mlflow.exceptions import MlflowException

from mlflow import MlflowClient

DATASET_FILENAME = "SeoulBikeData.csv"
SNAPSHOT_FILENAME = "snapshot.json"


def require_champion_absent(client: MlflowClient, registered_model: str) -> None:
    try:
        model = client.get_registered_model(registered_model)
    except MlflowException as error:
        if error.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise
        return
    if "champion" in model.aliases:
        raise ValueError("Bootstrap requires an absent champion; use the ordinary relative workflow.")


def snapshot_champion(client: MlflowClient, registered_model: str) -> dict:
    version = client.get_model_version_by_alias(registered_model, "champion")
    if version.status != "READY" or not version.run_id or not version.source:
        raise ValueError("Champion must be READY with a source run and model URI.")
    if client.get_run(version.run_id).info.status != "FINISHED":
        raise ValueError("Champion source run must be FINISHED.")
    return {
        "registered_model": registered_model,
        "version": str(version.version),
        "run_id": version.run_id,
        "source": version.source,
        "model_uri": f"models:/{registered_model}/{version.version}",
    }


def prepare_snapshot(
    client: MlflowClient,
    registered_model: str,
    dataset: Path,
    output_directory: Path,
    git_commit: str,
    bootstrap: bool = False,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", git_commit):
        raise ValueError("Snapshot requires the full Git commit.")
    if any(output_directory.iterdir()):
        raise ValueError("Snapshot output directory must be empty.")
    if bootstrap:
        require_champion_absent(client, registered_model)
        champion = None
    else:
        champion = snapshot_champion(client, registered_model)
    content = dataset.read_bytes()
    if not content or content.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError("Dataset must contain CSV data, not an empty file or LFS pointer.")
    snapshot = {
        "git_commit": git_commit,
        "mode": "bootstrap" if bootstrap else "relative",
        "registered_model": registered_model,
        "champion": champion,
        "dataset": {
            "filename": DATASET_FILENAME,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        },
    }
    copied_dataset = output_directory / DATASET_FILENAME
    with copied_dataset.open("xb") as stream:
        stream.write(content)
    copied_dataset.chmod(0o444)
    manifest = output_directory / SNAPSHOT_FILENAME
    with manifest.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(snapshot, indent=2) + "\n")
    manifest.chmod(0o444)
    return snapshot


def load_snapshot(manifest: Path, content: bytes, git_commit: str, registered_model: str) -> dict:
    snapshot = json.loads(manifest.read_text(encoding="utf-8"))
    if snapshot["git_commit"] != git_commit:
        raise ValueError("Snapshot commit does not match the training commit.")
    dataset = snapshot["dataset"]
    if (
        dataset["filename"] != DATASET_FILENAME
        or dataset["sha256"] != hashlib.sha256(content).hexdigest()
        or dataset["size_bytes"] != len(content)
    ):
        raise ValueError("Dataset does not match the initial snapshot.")
    champion = snapshot["champion"]
    if snapshot["registered_model"] != registered_model:
        raise ValueError("Snapshot registered model does not match training.")
    if snapshot["mode"] == "bootstrap":
        if champion is not None:
            raise ValueError("Bootstrap snapshot cannot contain a champion.")
        return snapshot
    if snapshot["mode"] != "relative" or champion is None:
        raise ValueError("Invalid snapshot evaluation mode.")
    if (
        champion["registered_model"] != registered_model
        or not re.fullmatch(r"[1-9][0-9]*", champion["version"])
        or champion["model_uri"] != f"models:/{registered_model}/{champion['version']}"
        or not champion["run_id"]
        or not champion["source"]
    ):
        raise ValueError("Invalid champion snapshot.")
    return snapshot
