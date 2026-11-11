import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clean-test-set.py"


class CleanTestSetTests(unittest.TestCase):
    def test_only_named_execution_is_deleted_and_missing_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "test-set-123-1"
            selected.mkdir()
            (selected / "data.json").write_text("data")
            other = root / "test-set-456-1"
            other.mkdir()
            for _ in range(2):
                subprocess.run(
                    [sys.executable, str(SCRIPT), "--runner-temp", str(root), "--workspace", selected.name],
                    check=True,
                    capture_output=True,
                )
                self.assertFalse(selected.exists())
                self.assertTrue(other.is_dir())

    def test_invalid_names_cannot_delete_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("..", ".", "", "test-set-123-1/..", "arbitrary"):
                with self.subTest(name=name):
                    process = subprocess.run(
                        [sys.executable, str(SCRIPT), "--runner-temp", directory, "--workspace", name],
                        capture_output=True,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    self.assertTrue(Path(directory).is_dir())


if __name__ == "__main__":
    unittest.main()
