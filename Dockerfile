# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

ARG INCLUDE_SEMANTIC_CACHE=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first to leverage layer caching.
COPY requirements.txt requirements-semantic.txt ./
RUN set -eux; \
    python -m pip install --upgrade pip; \
    pip install -r requirements.txt; \
    if [ "$INCLUDE_SEMANTIC_CACHE" = "1" ]; then \
      pip install -r requirements-semantic.txt; \
    fi

# Copy the application code and catalog assets.
COPY app ./app
COPY config ./config
COPY generated ./generated

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
