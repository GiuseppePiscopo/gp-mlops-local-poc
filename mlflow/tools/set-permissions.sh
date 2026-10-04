#!/bin/bash

sudo chown -R "$USER":"$USER" /opt/mlops-local-poc

find /opt/mlops-local-poc/mlflow -type d -exec chmod 755 {} + && \
find /opt/mlops-local-poc/mlflow -type f -exec chmod 644 {} + && \
find /opt/mlops-local-poc/mlflow/scripts -type f -name '*.sh' -exec chmod 755 {} + 2>/dev/null || true

chmod 600 /opt/mlops-local-poc/mlflow/.env
