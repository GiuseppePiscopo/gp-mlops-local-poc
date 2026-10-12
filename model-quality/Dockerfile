FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /requirements.txt

RUN python -m pip install --upgrade \
      pip==26.2.1 \
    && python -m pip install \
      -r /requirements.txt

COPY scripts/ /scripts/

WORKDIR /scripts
