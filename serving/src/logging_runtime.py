"""MLflow runtime with structured request/response logging.

Each successful inference appends one flat JSONL record to stdout:

  {"timestamp": ..., "model": ..., "version": ..., "request_id": ...,
   "inputs": [...], "outputs": [...]}

Sampling is controlled by LOG_SAMPLE_RATE (0.0-1.0). Logging never breaks
inference: any error is caught and reported without touching the response.
"""

import json
import logging
import os
import random
import sys
import time
import uuid

from mlserver.types import InferenceRequest, InferenceResponse
from mlserver_mlflow import MLflowRuntime

_logger = logging.getLogger("inference-logging")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(logging.INFO)
_logger.propagate = False

MODEL_NAME = os.getenv("MODEL_NAME", "gp-mof-capstone-model")
MODEL_VERSION = os.getenv("MODEL_VERSION", "unknown")
SAMPLE_RATE = float(os.getenv("LOG_SAMPLE_RATE", "1.0"))


def _tensor_data(data):
    for attr in ("root", "__root__"):
        if hasattr(data, attr):
            return getattr(data, attr)
    if isinstance(data, (list, tuple)):
        return list(data)
    try:
        return list(data)
    except TypeError:
        return str(data)


def _as_dict(items):
    return [
        {
            "name": item.name,
            "datatype": item.datatype,
            "shape": list(item.shape),
            "data": _tensor_data(item.data),
        }
        for item in items
    ]


class LoggingMLflowRuntime(MLflowRuntime):
    async def predict(self, request: InferenceRequest) -> InferenceResponse:
        response = await super().predict(request)
        try:
            if random.random() < SAMPLE_RATE:
                record = {
                    "timestamp": time.time(),
                    "model": MODEL_NAME,
                    "version": MODEL_VERSION,
                    "request_id": (
                        getattr(request, "id", None)
                        or getattr(response, "id", None)
                        or uuid.uuid4().hex
                    ),
                    "inputs": _as_dict(request.inputs),
                    "outputs": _as_dict(response.outputs),
                }
                _logger.info(json.dumps(record, default=str))
        except Exception:
            _logger.exception("inference logging failed")
        return response
