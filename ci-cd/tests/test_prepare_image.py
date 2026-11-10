import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_image

REAL_RUN = subprocess.run
IMAGE_ID = "sha256:" + "a" * 64


class PrepareImageTests(unittest.TestCase):
    def test_parse_arguments(self):
        with patch.object(
            sys, "argv", ["prepare_image.py", "resolve", "--repository", ".", "--component", "training", "--print-id"]
        ):
            arguments = prepare_image.parse_arguments()
        self.assertEqual(arguments.action, "resolve")
        self.assertEqual(arguments.repository, Path("."))
        self.assertEqual(arguments.component, "training")
        self.assertTrue(arguments.print_id)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        for component in prepare_image.IMAGE_NAMES:
            directory = self.repository / component
            directory.mkdir()
            (directory / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        for arguments in (
            ["init", "--quiet"],
            ["add", "."],
            [
                "-c",
                "user.name=Image test",
                "-c",
                "user.email=image-test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
        ):
            REAL_RUN(["git", *arguments], cwd=self.repository, check=True, capture_output=True)
        self.images = {}
        self.builds = 0
        self.tag_calls = 0
        self.built_labels = {}
        self.daemon_failure = False
        self.race = False
        self.original_command = prepare_image.command
        self.enterContext(patch.object(prepare_image, "command", side_effect=self.fake_command))
        self.enterContext(patch.object(prepare_image.subprocess, "run", side_effect=self.fake_run))

    def fake_command(self, arguments, *, cwd=None):
        if arguments[0] != "docker":
            return self.original_command(arguments, cwd=cwd)
        if self.daemon_failure:
            raise subprocess.CalledProcessError(1, arguments, stderr="daemon unavailable")
        if arguments[1:3] == ["image", "ls"]:
            return IMAGE_ID if arguments[-1] in self.images or (self.race and self.builds) else ""
        if arguments[1:3] == ["image", "inspect"]:
            if arguments[-1] not in self.images:
                raise subprocess.CalledProcessError(1, arguments, stderr="image missing")
            return json.dumps([self.images[arguments[-1]]])
        if arguments[1] == "tag":
            self.tag_calls += 1
            self.images[arguments[-1]] = {
                "Id": IMAGE_ID,
                "RepoDigests": [],
                "Config": {"Labels": self.built_labels},
            }
            return ""
        self.fail(f"Unexpected Docker command: {arguments}")

    def fake_run(self, arguments, **kwargs):
        if arguments[:2] != ["docker", "build"]:
            return REAL_RUN(arguments, **kwargs)
        self.builds += 1
        context = Path(arguments[-1])
        self.assertEqual((context / "Dockerfile").read_text(), "FROM scratch\n")
        self.built_labels = dict(
            arguments[index + 1].split("=", 1) for index, value in enumerate(arguments) if value == "--label"
        )
        Path(arguments[arguments.index("--iidfile") + 1]).write_text(IMAGE_ID, encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0)

    def prepare(self, action="build", component="training", override=None):
        return prepare_image.prepare_image(action, self.repository, component, override)

    def test_build_reuse_and_resolve_all_components(self):
        for component, name in prepare_image.IMAGE_NAMES.items():
            with self.subTest(component=component):
                result = self.prepare(component=component)
                self.assertEqual(result["image"], f"{name}:git-{result['git_commit']}")
                self.assertEqual(result["image_id"], IMAGE_ID)
                self.assertEqual(result["repo_digests"], [])
                self.assertFalse(result["reused"])
                self.assertTrue(self.prepare(component=component)["reused"])
                self.assertEqual(self.prepare("resolve", component)["image_id"], IMAGE_ID)
        self.assertEqual(self.builds, 3)
        self.assertEqual(self.tag_calls, 3)

    def test_dirty_tracked_source_rejected_before_docker(self):
        (self.repository / "training" / "Dockerfile").write_text("FROM changed\n")
        with self.assertRaisesRegex(ValueError, "must be clean"):
            self.prepare()
        self.assertEqual(self.builds, 0)

    def test_untracked_source_rejected(self):
        (self.repository / "new-source.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "must be clean"):
            self.prepare()

    def test_staged_source_rejected(self):
        (self.repository / "training" / "Dockerfile").write_text("FROM changed\n")
        REAL_RUN(["git", "add", "."], cwd=self.repository, check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "must be clean"):
            self.prepare()

    def test_explicit_rebuild_suffix(self):
        commit, _ = prepare_image.snapshot(self.repository, "training")
        result = self.prepare(override=f"gp-mof-capstone-training:git-{commit}-rebuild1")
        self.assertTrue(result["image"].endswith("-rebuild1"))

    def test_dev_and_wrong_commit_rejected(self):
        for reference in ("gp-mof-capstone-training:dev", "gp-mof-capstone-training:git-" + "0" * 40):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "Image must be"):
                self.prepare(override=reference)

    def test_wrong_labels_refuse_reuse_and_resolve(self):
        result = self.prepare()
        self.images[result["image"]]["Config"]["Labels"] = {}
        for action in ("build", "resolve"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "provenance mismatch"):
                self.prepare(action)
        self.assertEqual(self.builds, 1)
        self.assertEqual(self.tag_calls, 1)

    def test_missing_image_is_not_built_by_consumer(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare("resolve")
        self.assertEqual(self.builds, 0)

    def test_daemon_error_does_not_trigger_build(self):
        self.daemon_failure = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual(self.builds, 0)

    def test_new_tag_during_build_is_not_overwritten(self):
        self.race = True
        with self.assertRaisesRegex(ValueError, "appeared during build"):
            self.prepare()
        self.assertEqual(self.tag_calls, 0)

    def test_invalid_image_id_rejected(self):
        result = self.prepare()
        self.images[result["image"]]["Id"] = ""
        with self.assertRaisesRegex(ValueError, "image ID"):
            self.prepare("resolve")

    def test_optional_registry_digest_preserved(self):
        result = self.prepare()
        digest = "example.invalid/training@" + IMAGE_ID
        self.images[result["image"]]["RepoDigests"] = [digest]
        self.assertEqual(self.prepare("resolve")["repo_digests"], [digest])


class TrainingConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "training" / "scripts" / "run_training.py"
        spec = importlib.util.spec_from_file_location("training_launcher", path)
        cls.launcher = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.launcher
        spec.loader.exec_module(cls.launcher)

    def test_resolution_failure_is_explicit(self):
        with patch.object(
            self.launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "", "dirty")
        ):
            with self.assertRaisesRegex(self.launcher.PreRunValidationError, "dirty"):
                self.launcher.get_image_identity(Path("repository"), None)

    def test_local_identity_without_registry_digest(self):
        identity = {"image": "training:git-test", "image_id": IMAGE_ID, "repo_digests": []}
        with patch.object(
            self.launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(identity), "")
        ):
            self.assertEqual(self.launcher.get_image_identity(Path("repository"), None), identity)

    def test_run_uses_image_id_and_optional_digest(self):
        context = self.launcher.RunContext(
            image_tag="training:git-test",
            image_id=IMAGE_ID,
            image_digest="",
            mlflow_tracking_uri="http://mlflow:5000",
            experiment_name="test",
            docker_network="test",
            registered_model_name="test",
            training_data_path=Path("data.csv"),
            git_repository_path=Path("repository"),
            n_estimators=100,
            max_depth=10,
            git_commit="a" * 40,
            git_remote="test",
            git_dirty="false",
        )
        command = self.launcher.build_docker_command(context)
        self.assertIn(IMAGE_ID, command)
        self.assertIn(f"CONTAINER_IMAGE_ID={IMAGE_ID}", command)
        self.assertFalse(any(value.startswith("CONTAINER_IMAGE_DIGEST=") for value in command))


if __name__ == "__main__":
    unittest.main()
