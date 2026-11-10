import argparse
import importlib.util
import sys
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
    def test_training_registration_modes(self):
        for skip in (False, True):
            with self.subTest(skip_registration=skip):
                args = argparse.Namespace(
                    training_data="dataset.csv",
                    n_estimators=100,
                    max_depth=10,
                    registered_model_name="test-model",
                    skip_registration=skip,
                    fail_for_alert_test=False,
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
                    train.main(args)
                    evaluate.assert_called_once()
                    mlflow.log_params.assert_called()
                    mlflow.log_dict.assert_called_once()
                    mlflow.sklearn.log_model.assert_called_once()
                    self.assertEqual(
                        mlflow.sklearn.log_model.call_args.kwargs["registered_model_name"],
                        None if skip else "test-model",
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


if __name__ == "__main__":
    unittest.main()
