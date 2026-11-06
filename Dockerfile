# ==============================================================================
# Multi-Stage Production Dockerfile for Stock Analysis System
# Hardened according to CIS Docker Benchmark & Container Security Standards
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build & Dependency Wheel Cache
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Set build-time environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies securely and purge apt caches
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install uv package manager with pinned version
COPY --from=ghcr.io/astral-sh/uv:0.5.24 /uv /bin/uv

# Copy project manifests for frozen dependency resolution
COPY pyproject.toml uv.lock ./

# Compile virtualenv with frozen dependencies
RUN uv venv /opt/venv && \
    uv sync --frozen --no-dev --no-install-project

# ------------------------------------------------------------------------------
# Stage 2: Runtime Image (Minimal Attack Surface & Non-Root Execution)
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production

WORKDIR /app

# Create unprivileged group and user (UID/GID 10001)
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -M appuser && \
    chown -R appuser:appgroup /app

# Copy virtualenv and application source from builder
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup migrations/ /app/migrations/
COPY --chown=appuser:appgroup alembic.ini /app/alembic.ini

# Expose internal API port
EXPOSE 8000

# Set healthcheck for container liveness monitoring
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# Switch to unprivileged non-root user (CIS-DI-001)
USER 10001:10001

# Run ASGI server with least privileges
CMD ["uvicorn", "stock_analysis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
