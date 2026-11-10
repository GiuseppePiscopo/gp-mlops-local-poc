import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from random_forest import train

LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "run_training.py"
SPEC = importlib.util.spec_from_file_location("training_launcher", LAUNCHER)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


class TrainingRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(launcher, "print_run_summary"))

    def test_training_registration_modes(self):
        for skip in (False, True):
            with self.subTest(skip_registration=skip), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "result.json"
                args = argparse.Namespace(
                    training_data="dataset.csv",
                    n_estimators=100,
                    max_depth=10,
                    registered_model_name="test-model",
                    skip_registration=skip,
                    fail_for_alert_test=False,
                    result_json=output,
                    evaluate_champion=False,
                )
                features = MagicMock()
                with (
                    patch.dict("os.environ", {"MLFLOW_TRACKING_URI": "http://unused"}),
                    patch.object(train, "mlflow") as mlflow,
                    patch.object(train, "get_dataset_tags", return_value={}),
                    patch.object(train, "get_data", return_value=MagicMock()),
                    patch.object(train, "prepare_data", return_value=(features,) * 4),
                    patch.object(train, "train_model"),
                    patch.object(train, "eval_model") as evaluate,
                    patch.object(train, "infer_signature"),
                ):
                    mlflow.start_run.return_value.__enter__.return_value.info.run_id = "run-123"
                    mlflow.sklearn.log_model.return_value.model_uri = "models:/m-logged-model"
                    mlflow.start_run.return_value.__exit__.side_effect = lambda *args, output=output: self.assertFalse(
                        output.exists()
                    )
                    train.main(args)
                    evaluate.assert_called_once()
                    mlflow.log_params.assert_called()
                    mlflow.log_dict.assert_called_once()
                    mlflow.sklearn.log_model.assert_called_once()
                    self.assertEqual(
                        mlflow.sklearn.log_model.call_args.kwargs["registered_model_name"],
                        None if skip else "test-model",
                    )
                    self.assertEqual(
                        json.loads(output.read_text()),
                        {
                            "run_id": "run-123",
                            "model_uri": "models:/m-logged-model",
                            "git_commit": train.os.getenv("GIT_COMMIT", "unknown"),
                        },
                    )

    def test_cli_and_launcher_forward_registration_mode(self):
        for skip in (False, True):
            with self.subTest(skip_registration=skip):
                with patch.object(sys, "argv", ["train"] + (["--skip-registration"] if skip else [])):
                    args = launcher.parse_args()
                self.assertEqual(args.skip_registration, skip)
                context = MagicMock(skip_registration=skip, image_digest="")
                command = launcher.build_docker_command(context)
                self.assertEqual("--skip_registration" in command, skip)
                with patch.object(
                    sys,
                    "argv",
                    ["train", "--training_data", "dataset.csv"] + (["--skip_registration"] if skip else []),
                ):
                    self.assertEqual(train.parse_args().skip_registration, skip)

    def test_training_failure_propagates_without_model_logging(self):
        args = argparse.Namespace(
            training_data="dataset.csv",
            n_estimators=100,
            max_depth=10,
            skip_registration=True,
            fail_for_alert_test=False,
            result_json=None,
            evaluate_champion=False,
        )
        with (
            patch.dict("os.environ", {"MLFLOW_TRACKING_URI": "http://unused"}),
            patch.object(train, "mlflow") as mlflow,
            patch.object(train, "get_dataset_tags", return_value={}),
            patch.object(train, "get_data", side_effect=RuntimeError("Dataset read failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Dataset read failed"):
                train.main(args)
            mlflow.sklearn.log_model.assert_not_called()

    def test_launcher_exports_result_and_cleans_temporary_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = MagicMock(
                git_commit="a" * 40,
                git_repository_path=root / "checkout",
                skip_registration=True,
                image_digest="",
            )
            result = {"run_id": "run-123", "model_uri": "models:/m-123", "git_commit": context.git_commit}
            mount_files = []

            def execute(command):
                mount = next(
                    value
                    for value in command
                    if isinstance(value, str) and value.endswith(f"dst={launcher.CONTAINER_RESULT_PATH}")
                )
                source = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
                mount_files.append(source)
                self.assertIn("--result_json", command)
                source.write_text(json.dumps(result), encoding="utf-8")

            output = root / "output" / "result.json"
            with patch.object(launcher, "execute_docker_command", side_effect=execute):
                launcher.execute_training(context, output)
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertFalse(mount_files[0].exists())

    def test_launcher_rejects_missing_mismatched_and_failed_results(self):
        for scenario in ("missing", "wrong_commit", "container_failure"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                context = MagicMock(
                    git_commit="a" * 40,
                    git_repository_path=root / "checkout",
                    skip_registration=True,
                    image_digest="",
                )
                output = root / "result.json"

                def execute(command, scenario=scenario):
                    if scenario == "container_failure":
                        raise SystemExit(7)
                    if scenario == "wrong_commit":
                        mount = next(
                            v
                            for v in command
                            if isinstance(v, str) and v.endswith(f"dst={launcher.CONTAINER_RESULT_PATH}")
                        )
                        source = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
                        source.write_text(
                            json.dumps({"run_id": "run", "model_uri": "models:/m", "git_commit": "wrong"})
                        )

                with patch.object(launcher, "execute_docker_command", side_effect=execute):
                    with self.assertRaises((ValueError, launcher.PreRunValidationError, SystemExit)):
                        launcher.execute_training(context, output)
                self.assertFalse(output.exists())

    def test_launcher_rejects_checkout_output_and_existing_result_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = MagicMock(git_repository_path=root / "checkout")
            existing = root / "result.json"
            existing.write_text("previous")
            with patch.object(launcher, "execute_docker_command") as execute:
                for output in (root / "checkout" / "result.json", existing):
                    with self.assertRaises(launcher.PreRunValidationError):
                        launcher.execute_training(context, output)
                execute.assert_not_called()
            self.assertEqual(existing.read_text(), "previous")

    def test_gate_failure_exports_result_and_preserves_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = MagicMock(
                git_commit="a" * 40,
                git_repository_path=root / "checkout",
                skip_registration=True,
                evaluate_champion=True,
                image_digest="",
            )
            result = {
                "run_id": "run",
                "model_uri": "models:/m",
                "git_commit": context.git_commit,
                "evaluation": {"passed": False},
            }

            def execute(command):
                mount = next(
                    v for v in command if isinstance(v, str) and v.endswith(f"dst={launcher.CONTAINER_RESULT_PATH}")
                )
                Path(mount.split("src=", 1)[1].split(",dst=", 1)[0]).write_text(json.dumps(result))
                self.assertIn("--evaluate_champion", command)
                raise SystemExit(1)

            output = root / "result.json"
            with patch.object(launcher, "execute_docker_command", side_effect=execute):
                with self.assertRaises(SystemExit) as failure:
                    launcher.execute_training(context, output)
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(json.loads(output.read_text()), result)


if __name__ == "__main__":
    unittest.main()
