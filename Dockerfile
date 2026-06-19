# Megatron Dockerfile
# Multi-stage build for smaller image size

# Stage 1: Build dependencies
FROM python:3.10-slim as builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system -e "."

# Stage 2: Runtime image
FROM python:3.10-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY mcp_servers/ ./mcp_servers/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY config/ ./config/

# Create data directory
RUN mkdir -p /app/data

# Environment variables
ENV PYTHONPATH=/app/src
ENV DATABASE_URL=sqlite:///app/megatron.db
ENV SECRET_KEY=change-me-in-production
ENV ADMIN_TOKEN=admin

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run migrations and start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn megatron.web.app:app --host 0.0.0.0 --port 8000"]
