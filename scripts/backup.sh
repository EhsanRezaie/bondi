#!/bin/bash
set -e

BACKUP_DIR="/opt/demo-bondi/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

DB_USER="${POSTGRES_USER:-bondi_admin}"
DB_NAME="${POSTGRES_DB:-bondi}"

mkdir -p "$BACKUP_DIR"

echo "=== Database Backup: $TIMESTAMP ==="

# Dump database
docker exec bondi_db pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"

# Remove backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "✅ Backup saved: $BACKUP_DIR/db_$TIMESTAMP.sql.gz"
echo "   Retaining last $KEEP_DAYS days of backups."
