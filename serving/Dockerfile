FROM python:3.11.17-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MLSERVER_METRICS_DIR=/tmp/mlserver-metrics \
    MLSERVER_ENVIRONMENTS_DIR=/tmp/mlserver-envs \
    MLSERVER_PARALLEL_WORKERS=0

RUN python -m pip install --upgrade \
      pip==26.2.1 \
      setuptools==79.0.1 \
      wheel==0.46.3 \
    && python -m pip install \
      mlserver==1.7.1 \
      mlserver-mlflow==1.7.1 \
      mlflow==3.14.0 \
      numpy==1.26.4 \
      pandas==2.2.3 \
      scikit-learn==1.6.1 \
      scipy==1.17.1 \
      skops==0.16.0

WORKDIR /mnt/models

COPY src/logging_runtime.py /opt/logging/logging_runtime.py

ENV PYTHONPATH=/opt/logging

EXPOSE 8080 8081

CMD ["mlserver", "start", "/mnt/models"]