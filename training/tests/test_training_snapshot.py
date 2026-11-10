import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from common.training_snapshot import DATASET_FILENAME, SNAPSHOT_FILENAME, load_snapshot, prepare_snapshot


class TrainingSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = self.root / "source.csv"
        self.content = b"feature,target\n1,2\n3,4\n"
        self.dataset.write_bytes(self.content)
        self.output = self.root / "snapshot"
        self.output.mkdir()
        self.client = MagicMock()
        self.client.get_model_version_by_alias.return_value = SimpleNamespace(
            status="READY", version="1", run_id="champion-run", source="models:/m-champion"
        )
        self.client.get_run.return_value.info.status = "FINISHED"
        self.commit = "a" * 40
        # Snapshot files are deliberately read-only; restore permissions for cleanup.
        self.addCleanup(self.restore_permissions)

    def restore_permissions(self):
        for path in self.output.iterdir():
            path.chmod(0o600)

    def prepare(self):
        return prepare_snapshot(self.client, "model", self.dataset, self.output, self.commit)

    def test_snapshot_fixes_csv_and_champion_and_marks_files_readonly(self):
        snapshot = self.prepare()
        self.dataset.write_text("changed original")
        self.client.get_model_version_by_alias.return_value.version = "2"
        copied = self.output / DATASET_FILENAME
        self.assertEqual(copied.read_bytes(), self.content)
        self.assertEqual(snapshot["dataset"]["sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(snapshot["champion"]["version"], "1")
        for path in self.output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        loaded = load_snapshot(self.output / SNAPSHOT_FILENAME, copied.read_bytes(), self.commit, "model")
        self.assertEqual(loaded, snapshot)
        self.client.get_model_version_by_alias.assert_called_once_with("model", "champion")

    def test_changed_csv_commit_or_model_is_rejected(self):
        self.prepare()
        manifest = self.output / SNAPSHOT_FILENAME
        for content, commit, model in (
            (b"changed copy", self.commit, "model"),
            (self.content, "b" * 40, "model"),
            (self.content, self.commit, "another-model"),
        ):
            with self.subTest(content=content, commit=commit, model=model):
                with self.assertRaises(ValueError):
                    load_snapshot(manifest, content, commit, model)

    def test_existing_output_is_not_overwritten(self):
        self.prepare()
        previous = (self.output / SNAPSHOT_FILENAME).read_bytes()
        with self.assertRaisesRegex(ValueError, "empty"):
            self.prepare()
        self.assertEqual((self.output / SNAPSHOT_FILENAME).read_bytes(), previous)

    def test_registry_failure_does_not_create_snapshot(self):
        self.client.get_model_version_by_alias.side_effect = RuntimeError("Registry unavailable")
        with self.assertRaisesRegex(RuntimeError, "Registry unavailable"):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_input_and_incoherent_manifest_fail(self):
        for content in (b"", b"version https://git-lfs.github.com/spec/v1\n"):
            self.dataset.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "CSV data"):
                self.prepare()
            self.assertEqual(list(self.output.iterdir()), [])
        self.dataset.write_bytes(self.content)
        snapshot = self.prepare()
        manifest = self.output / SNAPSHOT_FILENAME
        manifest.chmod(0o600)
        snapshot["champion"]["model_uri"] = "models:/model@champion"
        manifest.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "champion snapshot"):
            load_snapshot(manifest, self.content, self.commit, "model")


if __name__ == "__main__":
    unittest.main()
