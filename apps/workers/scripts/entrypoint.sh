#!/bin/sh
# Production entrypoint.
#
# Usage:
#   Default (start server):  docker run <image>
#   Run migrations:          docker run <image> migrate
#
# Environment variables:
#   WEB_CONCURRENCY  Number of uvicorn worker processes (default: 2)
set -e

case "${1:-}" in
  migrate)
    echo "Running Alembic migrations..."
    exec alembic upgrade head
    ;;
  *)
    exec uvicorn main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers "${WEB_CONCURRENCY:-2}"
    ;;
esac
