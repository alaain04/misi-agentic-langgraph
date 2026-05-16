# =============================================================================
# Stage 1: builder — install dependencies
# =============================================================================
FROM docker.io/python:3.12-slim AS builder

WORKDIR /build

# System dependencies for asyncpg / cryptography compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# =============================================================================
# Stage 2: runtime image
# =============================================================================
FROM docker.io/python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    WEB_CONCURRENCY=2

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY . .
RUN chown -R appuser:appgroup /app && chmod -R 550 /app && chmod 550 /app/scripts/entrypoint.sh

USER appuser

EXPOSE 8000

CMD ["/app/scripts/entrypoint.sh"]
