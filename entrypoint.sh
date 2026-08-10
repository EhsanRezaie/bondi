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

    # Automatic worker count, tuned to the box: scale with cores but never
    # commit more RAM than the machine actually has (~250MB per async worker).
    # Override to pin a value with WEB_WORKERS.
    if [ -n "$WEB_WORKERS" ]; then
        WORKERS="$WEB_WORKERS"
    else
        AVAILABLE_MB=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 2048)
        WORKERS=$(( (CPU_CORES * 2) + 1 ))
        MAX_BY_RAM=$(( AVAILABLE_MB / 250 ))
        if [ "$WORKERS" -gt "$MAX_BY_RAM" ]; then
            WORKERS="$MAX_BY_RAM"
        fi
        if [ "$WORKERS" -gt 8 ]; then
            WORKERS=8
        fi
        if [ "$WORKERS" -lt 1 ]; then
            WORKERS=1
        fi
    fi

    echo "Starting app (production, $CPU_CORES cores, $WORKERS workers)..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $WORKERS --timeout-graceful-shutdown 30
fi
