#!/bin/sh
set -e

echo "Applying migrations..."
alembic upgrade head

echo "Seeding interests..."
python -m app.db.scripts.seed_interests

echo "Seeding prompts..."
python -m app.db.scripts.seed_propmts

# Add --reload in development
if [ "$ENVIRONMENT" = "development" ]; then
    echo "Starting app (dev mode with --reload)..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
else
    echo "Starting app (production)..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
