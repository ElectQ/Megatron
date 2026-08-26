# Megatron Dockerfile

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps directly (simpler than multi-stage for this project size).
# Versions are PINNED to the combination verified working on Python 3.10 (local
# dev venv, 2026-08). Unpinned `>=` ranges let a rebuild pull a package release
# that drops Python 3.10 support (e.g. `from typing import NotRequired`, which is
# 3.11+), taking the whole container down. Bump deliberately, and re-verify on
# python:3.10 before merging.
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system "fastapi==0.137.1" "uvicorn[standard]==0.49.0" \
        "sqlalchemy[asyncio]==2.0.51" "aiosqlite==0.22.1" "alembic==1.18.4" \
        "pydantic==2.13.4" "pydantic-settings==2.14.1" "apscheduler==3.11.2" \
        "litellm==1.89.1" "httpx==0.28.1" "jinja2==3.1.6" \
        "itsdangerous==2.2.0" "bcrypt==5.0.0" "cryptography==49.0.0" \
        "structlog==26.1.0" "python-multipart==0.0.32" "mcp==1.28.1" \
        "asyncpg==0.31.0" \
        "trafilatura==2.1.0" \
        "pyyaml==6.0.3" "feedparser==6.0.12" "jsonschema==4.26.0"

# Copy application
COPY src/ ./src/
COPY mcp_servers/ ./mcp_servers/
COPY migrations/ ./migrations/
COPY config/ ./config/
COPY alembic.ini ./

ENV PYTHONPATH=/app:/app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn megatron.web.app:app --host 0.0.0.0 --port 8000"]
