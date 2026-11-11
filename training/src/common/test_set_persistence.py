"""Persist and restore the exact numeric test set used by training."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from common import training_utils

DATA_FILENAME = "test-data.json"
MANIFEST_FILENAME = "test-set-manifest.json"


def test_set_identity(dataset_sha256: str, features: pd.DataFrame, target: pd.Series) -> dict:
    values = pd.concat([features, target], axis=1)
    return {
        "dataset_sha256": dataset_sha256,
        "random_seed": training_utils.RANDOM_SEED,
        "test_size": training_utils.TEST_SIZE,
        "preprocessing_sha256": hashlib.sha256(Path(training_utils.__file__).read_bytes()).hexdigest(),
        "feature_names": features.columns.tolist(),
        "feature_dtypes": [str(dtype) for dtype in features.dtypes],
        "feature_columns_name": features.columns.name,
        "target_name": target.name,
        "target_dtype": str(target.dtype),
        "index_name": features.index.name,
        "index_dtype": str(features.index.dtype),
        "test_row_indices": features.index.tolist(),
        "test_values_sha256": hashlib.sha256(
            pd.util.hash_pandas_object(values, index=True).values.tobytes()
        ).hexdigest(),
        "test_rows": len(features),
    }


def snapshot_checksum(snapshot: dict) -> str:
    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def save_test_set(directory: Path, features: pd.DataFrame, target: pd.Series, candidate: dict, snapshot: dict) -> dict:
    if not features.index.equals(target.index) or features.empty or not features.columns.is_unique:
        raise ValueError("Test features and target require aligned rows and unique feature names.")
    if candidate["git_commit"] != snapshot["git_commit"]:
        raise ValueError("Test set candidate commit does not match the initial snapshot.")
    payload = {
        "index": features.index.tolist(),
        "index_dtype": str(features.index.dtype),
        "index_name": features.index.name,
        "columns": features.columns.tolist(),
        "columns_name": features.columns.name,
        "dtypes": [str(dtype) for dtype in features.dtypes],
        "features": {column: features[column].tolist() for column in features.columns},
        "target": target.tolist(),
        "target_name": target.name,
        "target_dtype": str(target.dtype),
    }
    content = (json.dumps(payload, allow_nan=False) + "\n").encode("utf-8")
    identity = test_set_identity(snapshot["dataset"]["sha256"], features, target)
    manifest = {
        "candidate": candidate,
        "input_snapshot_sha256": snapshot_checksum(snapshot),
        "test_set": identity,
        "data": {"filename": DATA_FILENAME, "sha256": hashlib.sha256(content).hexdigest()},
    }
    with (directory / DATA_FILENAME).open("xb") as stream:
        stream.write(content)
    with (directory / MANIFEST_FILENAME).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return manifest


def load_test_set(
    directory: Path, expected_manifest: dict, candidate: dict, snapshot: dict
) -> tuple[pd.DataFrame, pd.Series]:
    manifest = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    if (
        manifest != expected_manifest
        or manifest["candidate"] != candidate
        or manifest["input_snapshot_sha256"] != snapshot_checksum(snapshot)
        or candidate["git_commit"] != snapshot["git_commit"]
    ):
        raise ValueError("Test set manifest does not match the candidate and initial snapshot.")
    content = (directory / DATA_FILENAME).read_bytes()
    if manifest["data"] != {"filename": DATA_FILENAME, "sha256": hashlib.sha256(content).hexdigest()}:
        raise ValueError("Test set file checksum does not match the manifest.")
    payload = json.loads(content)
    index = pd.Index(payload["index"], dtype=payload["index_dtype"], name=payload["index_name"])
    features = pd.DataFrame(
        {
            column: pd.Series(payload["features"][column], index=index, dtype=dtype)
            for column, dtype in zip(payload["columns"], payload["dtypes"], strict=True)
        },
        index=index,
    )
    features.columns.name = payload["columns_name"]
    target = pd.Series(payload["target"], index=index, dtype=payload["target_dtype"], name=payload["target_name"])
    identity = test_set_identity(snapshot["dataset"]["sha256"], features, target)
    if identity != manifest["test_set"]:
        raise ValueError("Restored test set identity does not match the manifest.")
    return features, target
