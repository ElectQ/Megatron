# Megatron Dockerfile

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps directly (simpler than multi-stage for this project size)
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0" \
        "sqlalchemy[asyncio]>=2.0.0" "aiosqlite>=0.20.0" "alembic>=1.13.0" \
        "pydantic>=2.0.0" "pydantic-settings>=2.0.0" "apscheduler>=3.10.0" \
        "litellm>=1.40.0" "httpx>=0.27.0" "jinja2>=3.1.0" \
        "itsdangerous>=2.2.0" "bcrypt>=4.0.0" "cryptography>=42.0.0" \
        "structlog>=24.1.0" "python-multipart>=0.0.9" "mcp>=1.0.0" \
        "asyncpg>=0.29.0" \
        "trafilatura>=1.12.0"

# Copy application
COPY src/ ./src/
COPY mcp_servers/ ./mcp_servers/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV PYTHONPATH=/app:/app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn megatron.web.app:app --host 0.0.0.0 --port 8000"]
