import glob
import os

import numpy as np
import pandas as pd
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import train_test_split

import mlflow

TARGET_COLUMN = "Rented Bike Count"
CATEGORICAL_COLUMNS = ["Seasons", "Holiday", "Functioning Day"]
DROP_COLUMNS = ["Date"]
RANDOM_SEED = 42
TEST_SIZE = 0.20


def get_data(path: str) -> pd.DataFrame:
    print("Reading data...")

    if os.path.isdir(path):
        csv_files = glob.glob(os.path.join(path, "*.csv"))
        if not csv_files:
            raise RuntimeError(f"No CSV files found in provided data path: {path}")

        df = pd.concat((pd.read_csv(file) for file in csv_files), ignore_index=True)
    else:
        df = pd.read_csv(path)

    return df


def prepare_data(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    print("Preparing data...")

    df = df.drop(columns=DROP_COLUMNS)
    df = pd.get_dummies(df, columns=CATEGORICAL_COLUMNS, drop_first=True)

    X = df.drop(columns=[TARGET_COLUMN])
    y = df[TARGET_COLUMN]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_SEED,
    )

    return X_train, X_test, y_train, y_test


def eval_model(
    model,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> dict[str, float]:
    y_hat = model.predict(X_test)

    mae = mean_absolute_error(y_test, y_hat)
    rmse = np.sqrt(mean_squared_error(y_test, y_hat))
    r2 = r2_score(y_test, y_hat)

    nonzero_mask = y_test != 0
    mape = mean_absolute_percentage_error(
        y_test[nonzero_mask],
        y_hat[nonzero_mask],
    )

    metrics = {
        "mae": float(mae),
        "rmse": float(rmse),
        "r2": float(r2),
        "mape": float(mape),
    }

    print(f"MAE: {metrics['mae']}")
    print(f"RMSE: {metrics['rmse']}")
    print(f"R2: {metrics['r2']}")
    print(f"MAPE: {metrics['mape']}")

    mlflow.log_metrics(metrics)

    return metrics
