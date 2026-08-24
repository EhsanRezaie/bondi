#!/bin/bash
set -e

# ── Config ────────────────────────────────────────────────────────────────────
DB_USER="${POSTGRES_USER:-bondi_admin}"
DB_NAME="${POSTGRES_DB:-bondi}"
MINIO_USER="${MINIO_ROOT_USER:-bondi_minio}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-}"
COMPOSE_FILE="docker-compose.yml"
HEALTH_URL="http://localhost/health/ready"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo -e "\033[1;34m>>>\033[0m $1"; }
ok()   { echo -e "\033[1;32m✅\033[0m $1"; }
fail() { echo -e "\033[1;31m❌\033[0m $1"; }

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Error: $COMPOSE_FILE not found. Run this from the repo root."
    exit 1
fi

echo "=========================================="
echo " Fresh Start — reset everything to clean"
echo "=========================================="
echo ""
echo "This will:"
echo "  - DROP and recreate the PostgreSQL database ($DB_NAME)"
echo "  - Flush Redis (tokens, cooldowns, caches)"
echo "  - Wipe MinIO photos (public + private buckets)"
echo "  - Run migrations + re-seed interests/prompts"
echo "  - Restart the full stack"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# 1. Stop app workers so nothing writes during the reset
log "Stopping app/celery..."
docker compose stop app celery-worker celery-beat 2>/dev/null || true

# 2. Drop + recreate the database
log "Recreating database $DB_NAME..."
docker exec bondi_db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME WITH (FORCE);"
docker exec bondi_db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
ok "Database recreated"

# 3. Flush Redis (tokens, cooldowns, caches, online state)
log "Flushing Redis..."
docker exec bondi_redis redis-cli FLUSHALL 2>/dev/null || docker exec bondi_redis redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning FLUSHALL
ok "Redis flushed"

# 4. Wipe MinIO photos (both buckets), then re-create them via minio-init
log "Wiping MinIO buckets..."
docker run --rm --network bondi_internal minio/mc sh -c "
    mc alias set local http://minio:9000 '$MINIO_USER' '$MINIO_PASS' &&
    mc rb --force local/photos-public 2>/dev/null || true &&
    mc rb --force local/photos-private 2>/dev/null || true
" 2>/dev/null || true
ok "MinIO buckets cleared"

# 5. Apply migrations + seeds (entrypoint also seeds interests/prompts on boot)
log "Applying migrations..."
docker compose run --rm migrate
ok "Migrations applied"

# 6. Restart the full stack (minio-init re-creates the buckets)
log "Starting stack..."
docker compose up -d

# 7. Health check
log "Waiting for health..."
for i in $(seq 1 24); do
    sleep 5
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        ok "Fresh start complete — app is healthy"
        exit 0
    fi
    log "Health check attempt $i/24..."
done
fail "App did not become healthy in time — check logs: docker compose logs --tail=50 app"
exit 1
