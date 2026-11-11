import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from common.test_set_persistence import DATA_FILENAME, MANIFEST_FILENAME, load_test_set, save_test_set


class TestSetPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.index = pd.Index([5, 1, 8], name="original-row")
        self.features = pd.DataFrame(
            {
                "temperature": pd.Series([0.12345678901234568, -1.0, 1e-100], index=self.index, dtype="float64"),
                "hour": pd.Series([1, 23, 12], index=self.index, dtype="int64"),
                "holiday": pd.Series([True, False, True], index=self.index, dtype="bool"),
            }
        )
        self.target = pd.Series([100, 200, 300], index=self.index, dtype="int64", name="Rented Bike Count")
        self.candidate = {"run_id": "run", "model_uri": "models:/m-model", "git_commit": "a" * 40}
        self.snapshot = {
            "git_commit": "a" * 40,
            "dataset": {"sha256": "dataset-hash"},
            "champion": {"version": "1"},
        }

    def save(self):
        return save_test_set(self.directory, self.features, self.target, self.candidate, self.snapshot)

    def test_roundtrip_preserves_values_types_order_and_indices_exactly(self):
        manifest = self.save()
        features, target = load_test_set(self.directory, manifest, self.candidate, self.snapshot)
        pd.testing.assert_frame_equal(features, self.features, check_exact=True)
        pd.testing.assert_series_equal(target, self.target, check_exact=True)
        self.assertEqual(manifest["candidate"], self.candidate)
        self.assertEqual(manifest["test_set"]["dataset_sha256"], "dataset-hash")

    def test_file_tampering_is_rejected(self):
        manifest = self.save()
        (self.directory / DATA_FILENAME).write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_test_set(self.directory, manifest, self.candidate, self.snapshot)

    def test_altered_manifest_or_references_are_rejected(self):
        manifest = self.save()
        for candidate, snapshot in (
            ({**self.candidate, "run_id": "different"}, self.snapshot),
            ({**self.candidate, "model_uri": "models:/other"}, self.snapshot),
            (self.candidate, {**self.snapshot, "dataset": {"sha256": "other"}}),
            (self.candidate, {**self.snapshot, "champion": {"version": "2"}}),
        ):
            with self.subTest(candidate=candidate, snapshot=snapshot):
                with self.assertRaisesRegex(ValueError, "manifest"):
                    load_test_set(self.directory, manifest, candidate, snapshot)
        modified = json.loads((self.directory / MANIFEST_FILENAME).read_text())
        modified["data"]["sha256"] = "newly-accepted-hash"
        (self.directory / MANIFEST_FILENAME).write_text(json.dumps(modified))
        with self.assertRaisesRegex(ValueError, "manifest"):
            load_test_set(self.directory, manifest, self.candidate, self.snapshot)

    def test_schema_or_row_changes_are_detected_beyond_file_checksum(self):
        for field in ("dtypes", "index", "target_dtype"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = save_test_set(root, self.features, self.target, self.candidate, self.snapshot)
                payload = json.loads((root / DATA_FILENAME).read_text())
                if field == "dtypes":
                    payload[field][1] = "float64"
                elif field == "index":
                    payload[field] = [1, 5, 8]
                else:
                    payload[field] = "float64"
                content = json.dumps(payload).encode()
                (root / DATA_FILENAME).write_bytes(content)
                manifest["data"]["sha256"] = hashlib.sha256(content).hexdigest()
                (root / MANIFEST_FILENAME).write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "identity"):
                    load_test_set(root, manifest, self.candidate, self.snapshot)

    def test_missing_files_nonfinite_values_and_misaligned_rows_fail(self):
        with self.assertRaises(FileNotFoundError):
            load_test_set(self.directory, {}, self.candidate, self.snapshot)
        self.features.iloc[0, 0] = np.nan
        with self.assertRaises(ValueError):
            self.save()
        self.features.iloc[0, 0] = 1.0
        self.target = self.target.iloc[::-1]
        with self.assertRaisesRegex(ValueError, "aligned"):
            self.save()

    def test_existing_files_are_not_overwritten(self):
        self.save()
        with self.assertRaises(FileExistsError):
            self.save()


if __name__ == "__main__":
    unittest.main()
