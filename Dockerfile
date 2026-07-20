# syntax=docker/dockerfile:1

# Official Python 3.12 image from Docker Hub (matches .python-version and uv.lock)
FROM python:3.12-slim-bookworm

# Faster, quieter, reproducible installs
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Install uv (the fast package manager) into the base image
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) Install dependencies first (cached unless the lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) Copy the application code and install the project itself
COPY . .
RUN uv sync --frozen --no-dev

# Put the venv on PATH so `python` = the project interpreter
ENV PATH="/app/.venv/bin:$PATH"

# `docker run <image> daily` -> `python main.py daily`
# Defaults to `daily`; override with weekly / monday-summary / etc.
ENTRYPOINT ["python", "main.py"]
CMD ["daily"]
