import argparse
import os

import mlflow.sklearn
from common.training_utils import get_data, prepare_data

import mlflow

DEFAULT_MODEL_URI = "models:/gp-mof-capstone-model@candidate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--training_data",
        required=True,
        type=str,
    )
    parser.add_argument(
        "--model_uri",
        default=DEFAULT_MODEL_URI,
        type=str,
    )
    parser.add_argument(
        "--rows",
        default=5,
        type=int,
    )

    return parser.parse_args()


def main(args: argparse.Namespace) -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RuntimeError("MLFLOW_TRACKING_URI environment variable is required")

    mlflow.set_tracking_uri(tracking_uri)

    df = get_data(args.training_data)
    _, X_test, _, _ = prepare_data(df)

    model = mlflow.sklearn.load_model(args.model_uri)

    input_rows = X_test.head(args.rows)
    predictions = model.predict(input_rows)

    print(f"Model URI: {args.model_uri}")
    print(f"Input rows: {len(input_rows)}")
    print("Predictions:")

    for row_index, prediction in enumerate(predictions, start=1):
        print(f"  row={row_index} prediction={prediction}")


if __name__ == "__main__":
    main(parse_args())
