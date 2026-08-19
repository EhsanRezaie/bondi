#!/bin/bash
set -e

DB_USER="${POSTGRES_USER:-bondi_admin}"
DB_NAME="${POSTGRES_DB:-bondi}"

if [ -z "$1" ]; then
    echo "Usage: $0 <backup_file.sql.gz>"
    echo ""
    echo "Available backups:"
    ls -la /opt/demo-bondi/backups/db_*.sql.gz 2>/dev/null || echo "  No backups found"
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
docker exec bondi_db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME WITH (FORCE);"
docker exec bondi_db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# Restore
echo "Restoring data..."
gunzip -c "$BACKUP_FILE" | docker exec -i bondi_db psql -U "$DB_USER" -d "$DB_NAME"

# Start app
echo "Starting app..."
docker compose start app

echo ""
echo "✅ Restore complete!"
