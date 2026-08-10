#!/bin/sh
set -e

# If the container was started with an explicit command (e.g. celery worker/beat
# via `docker compose` command:), run it as-is instead of the web server.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Applying migrations..."
alembic upgrade head

echo "Seeding interests..."
python -m app.db.scripts.seed_interests

echo "Seeding prompts..."
python -m app.db.scripts.seed_propmts

# Add --reload only if development environment
if [ "$ENVIRONMENT" = "development" ]; then
    echo "Starting app (dev mode with --reload)..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
else
    CPU_CORES=$(nproc 2>/dev/null || echo 2)
    WORKERS=$(( (CPU_CORES * 2) + 1 ))
    if [ "$WORKERS" -gt 8 ]; then
        WORKERS=8
    fi
    echo "Starting app (production, $CPU_CORES cores, $WORKERS workers)..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $WORKERS --timeout-graceful-shutdown 30
fi
