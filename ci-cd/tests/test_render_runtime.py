import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "serving" / "scripts" / "render-runtime.py"
SPEC = importlib.util.spec_from_file_location("render_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RenderRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        self.output = self.repository / "runtime" / "20-serving-runtime.yaml"
        self.identity = {
            "image": "gp-mof-capstone-mlserver:git-" + "a" * 40,
            "image_id": "sha256:" + "b" * 64,
            "git_commit": "a" * 40,
        }
        self.template = (SCRIPT.parents[1] / "manifests" / "20-serving-runtime.yaml").read_text(encoding="utf-8")
        self.calls = []
        self.enterContext(patch.object(runtime.subprocess, "run", side_effect=self.fake_run))

    def fake_run(self, arguments, **kwargs):
        self.calls.append(arguments)
        if arguments[0] == sys.executable:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(self.identity), "")
        self.assertEqual(arguments, ["git", "show", f"{self.identity['git_commit']}:{runtime.TEMPLATE_PATH}"])
        self.assertEqual(kwargs["cwd"], self.repository)
        return subprocess.CompletedProcess(arguments, 0, self.template, "")

    def test_only_image_is_changed_and_output_persisted(self):
        result = runtime.render_runtime(self.repository, None, self.output)
        rendered = self.output.read_text(encoding="utf-8")
        self.assertEqual(rendered, self.template.replace(runtime.IMAGE_PLACEHOLDER, json.dumps(self.identity["image"])))
        self.assertNotIn(runtime.IMAGE_PLACEHOLDER, rendered)
        self.assertEqual(result["image_id"], self.identity["image_id"])
        self.assertEqual(result["manifest"], str(self.output))
        self.assertEqual(self.calls[0][-2:], ["--component", "serving"])
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_image_override_is_validated_by_helper(self):
        reference = self.identity["image"] + "-rebuild1"
        self.identity["image"] = reference
        runtime.render_runtime(self.repository, reference, self.output)
        self.assertEqual(self.calls[0][-2:], ["--image", reference])

    def test_failed_validation_preserves_existing_output(self):
        self.output.parent.mkdir()
        self.output.write_text("previous manifest", encoding="utf-8")
        with patch.object(
            runtime.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(2, ["prepare_image"], stderr="mismatch"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.render_runtime(self.repository, None, self.output)
        self.assertEqual(self.output.read_text(), "previous manifest")

    def test_missing_or_duplicate_placeholder_fails(self):
        for template in ("image: old", self.template + runtime.IMAGE_PLACEHOLDER):
            with self.subTest(template=template):
                self.template = template
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    runtime.render_runtime(self.repository, None, self.output)
                self.assertFalse(self.output.exists())

    def test_incomplete_identity_fails(self):
        del self.identity["image_id"]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            runtime.render_runtime(self.repository, None, self.output)
        self.assertEqual(len(self.calls), 1)

    def test_versioned_template_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            runtime.render_runtime(self.repository, None, self.repository / runtime.TEMPLATE_PATH)

    def test_output_replace_failure_cleans_temporary_file(self):
        self.output.mkdir(parents=True)
        with self.assertRaises(OSError):
            runtime.render_runtime(self.repository, None, self.output)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_parse_arguments(self):
        with patch.dict(
            runtime.os.environ, {"GIT_REPOSITORY": str(self.repository), "IMAGE_TAG": self.identity["image"]}
        ):
            with patch.object(sys, "argv", ["render-runtime.py"]):
                arguments = runtime.parse_arguments()
        self.assertEqual(arguments.git_repository, self.repository)
        self.assertEqual(arguments.image_tag, self.identity["image"])


if __name__ == "__main__":
    unittest.main()
