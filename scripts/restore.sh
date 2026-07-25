#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <backup_file.sql.gz>"
    echo ""
    echo "Available backups:"
    ls -la /opt/dating-app/backups/db_*.sql.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: File not found: $BACKUP_FILE"
    exit 1
fi

echo "=== Database Restore ==="
echo "Restoring from: $BACKUP_FILE"
echo ""

read -p "This will OVERWRITE the current database. Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Stop app to prevent writes
echo "Stopping app..."
docker compose stop app

# Drop and recreate database
echo "Recreating database..."
docker exec dating_db psql -U dating_user -d postgres -c "DROP DATABASE dating_db;"
docker exec dating_db psql -U dating_user -d postgres -c "CREATE DATABASE dating_db OWNER dating_user;"

# Restore
echo "Restoring data..."
gunzip -c "$BACKUP_FILE" | docker exec -i dating_db psql -U dating_user -d dating_db

# Start app
echo "Starting app..."
docker compose start app

echo ""
echo "✅ Restore complete!"
