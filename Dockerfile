# syntax=docker/dockerfile:1

# uv-based image, Python 3.12 (matches .python-version and uv.lock)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Faster, quieter, reproducible installs
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

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
