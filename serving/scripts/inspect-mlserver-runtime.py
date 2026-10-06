import importlib.util
import sys
from importlib.metadata import PackageNotFoundError, version

# Print the interpreter version used by the exact MLServer runtime image.
print(f"python={sys.version}")

# Report packages required by the MLflow/skops model package.
for package_name in ("mlserver", "mlflow", "scikit-learn", "skops"):
    try:
        print(f"{package_name}={version(package_name)}")
    except PackageNotFoundError:
        print(f"{package_name}=NOT_INSTALLED")

# Report whether the MLflow model implementation is importable.
print(f"mlserver_mlflow_importable={importlib.util.find_spec('mlserver_mlflow') is not None}")
