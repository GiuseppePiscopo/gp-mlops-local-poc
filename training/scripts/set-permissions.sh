#!/bin/bash

sudo chown -R "$USER":"$USER" /opt/mlops-local-poc

find /opt/mlops-local-poc/training -type d -exec chmod 755 {} + && \
find /opt/mlops-local-poc/training -type f -exec chmod 644 {} + && \
find /opt/mlops-local-poc/training/scripts -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod 755 {} +
