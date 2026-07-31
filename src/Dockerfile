# ============================================
# EV Trip Optimizer Backend - Production Container
# ============================================

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install base system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    apt-transport-https \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft ODBC Driver for Azure SQL
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        unixodbc \
        msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# uv settings for Docker
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev deps in production)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Install the project itself
RUN uv sync --frozen --no-dev

# Create non-root user
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Bootstrap the DB (idempotent: stamps + create_all on fresh DBs, upgrade head
# on existing ones), then start uvicorn. Schema migration runs from exactly one
# app: the worker (PROCESS_ROLE=worker) skips it so it can't race the web app's
# Alembic upgrade on deploy. The same image/CMD serves all roles; the role comes
# from the PROCESS_ROLE env var, read by app.main.
CMD ["sh", "-c", "if [ \"$PROCESS_ROLE\" != \"worker\" ]; then uv run python -m scripts.db_bootstrap; fi && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
