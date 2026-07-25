#!/bin/bash
set -e

BACKUP_DIR="/opt/dating-app/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "=== Database Backup: $TIMESTAMP ==="

# Dump database
docker exec dating_db pg_dump -U dating_user -d dating_db | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"

# Remove backups older than KEEP_DAYS
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "✅ Backup saved: $BACKUP_DIR/db_$TIMESTAMP.sql.gz"
echo "   Retaining last $KEEP_DAYS days of backups."
