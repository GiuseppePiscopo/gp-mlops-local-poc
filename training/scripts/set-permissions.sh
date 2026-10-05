#!/bin/bash

sudo chown -R "$USER":"$USER" /opt/mlops-local-poc

find /opt/mlops-local-poc/training -type d -exec chmod 755 {} + && \
find /opt/mlops-local-poc/training -type f -exec chmod 644 {} + && \
find /opt/mlops-local-poc/training/scripts -type f -name '*.sh' -exec chmod 755 {} + 2>/dev/null || true

chmod 600 /opt/mlops-local-poc/training/.env
