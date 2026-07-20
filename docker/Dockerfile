# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# AetherSeed runtime image (API + worker share this image; command differs).
# Core dependencies only — the local AI path talks to Ollama over HTTP, so no
# heavy ML libraries are baked in. Add `.[ai]` / `.[browser]` if you need the
# local embedding stack or a bundled browser.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_PYTHON=1

# uv for fast, reproducible installs.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md ./
COPY aetherseed ./aetherseed
COPY db ./db
COPY prompts ./prompts
COPY alembic.ini ./
RUN uv pip install --system --no-cache ".[queue,postgres]"

# Non-root runtime user; writable data volume.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1

# Default: the API. The worker service overrides this in docker-compose.
CMD ["uvicorn", "aetherseed.apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
