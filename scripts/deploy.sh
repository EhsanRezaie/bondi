#!/bin/bash
set -e

# ── Config ────────────────────────────────────────────────────────────────────
DEPLOY_DIR="/opt/demo-bondi"
HEALTH_URL="http://localhost/health/ready"
HEALTH_RETRIES=24
HEALTH_INTERVAL=10
ROLLBACK_IMAGE="bondi-app:rollback"
COMPOSE_FILE="docker-compose.yml"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo -e "\033[1;34m>>>\033[0m $1"; }
ok()   { echo -e "\033[1;32m✅\033[0m $1"; }
fail() { echo -e "\033[1;31m❌\033[0m $1"; }

wait_for_health() {
    for i in $(seq 1 $HEALTH_RETRIES); do
        sleep $HEALTH_INTERVAL
        if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
            return 0
        fi
        log "Health check attempt $i/$HEALTH_RETRIES..."
    done
    return 1
}

rollback() {
    fail "Health check failed — rolling back..."
    docker compose down 2>/dev/null || true

    python3 -c "
import yaml, sys
with open('$COMPOSE_FILE') as f:
    data = yaml.safe_load(f)
if 'app' in data.get('services', {}):
    data['services']['app'].pop('build', None)
    data['services']['app']['image'] = '$ROLLBACK_IMAGE'
with open('$COMPOSE_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
" 2>/dev/null || sed -i "s|build: .*|image: $ROLLBACK_IMAGE|" "$COMPOSE_FILE"

    docker compose up -d
    log "Rollback complete — previous version restored"
}

cleanup_rollback_file() {
    git checkout -- "$COMPOSE_FILE" 2>/dev/null || true
}

# ── Deploy ────────────────────────────────────────────────────────────────────
cd "$DEPLOY_DIR"
log "Deploy started at $(date '+%Y-%m-%d %H:%M:%S')"

# 1. Save current image for rollback
CURRENT_IMAGE=$(docker compose images -q app 2>/dev/null | head -1)
if [ -n "$CURRENT_IMAGE" ]; then
    docker tag "$CURRENT_IMAGE" "$ROLLBACK_IMAGE"
    log "Rollback image saved"
else
    log "No existing image — first deploy, rollback not available"
fi

# 2. Sync to origin/main — force-sync. The repository is the source of truth
#    for all tracked files; any local drift (manual edits, leftover files) is
#    discarded so deploys are never blocked. Gitignored runtime files (.env,
#    firebase-service-account.json) are untouched by reset/clean.
log "Force-syncing to origin/main..."
git fetch origin main
if [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: server had local drift — overwriting from origin/main."
    git status --porcelain
fi
git reset --hard origin/main
git clean -fd
git checkout -B main origin/main

# 3. Check FCM service account file
if [ ! -f firebase-service-account.json ]; then
    fail "firebase-service-account.json not found — push notifications will be disabled"
    log "Place the file in $DEPLOY_DIR/ or set FCM_SERVICE_ACCOUNT_PATH in .env"
fi

# 4. Login to Docker Hub (if credentials provided). Best-effort only: this
#    deployment builds images locally and never pushes to Docker Hub, so an
#    unreachable registry (e.g. blocked/timeout on some hosts) must not abort.
if [ -n "$DOCKERHUB_USERNAME" ] && [ -n "$DOCKERHUB_TOKEN" ]; then
    log "Logging in to Docker Hub (best-effort)..."
    if echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin >/dev/null 2>&1; then
        ok "Docker Hub login OK"
    else
        log "Docker Hub unreachable — continuing (local build, no push)"
    fi
fi

# 4. Build/refresh base image (deps) — no-op unless requirements.txt changed
log "Ensuring base image (deps)..."
bash scripts/build-base.sh

# 5. Build new images (thin — skips pip, takes seconds).
#    All code-bearing services share the same Dockerfile; build them together so
#    celery worker/beat get the same code as the API.
log "Building..."
docker compose build app migrate celery-worker celery-beat

# 5b. Apply migrations to the database (one-shot, direct to db — not pgbouncer)
log "Applying migrations..."
docker compose run --rm migrate

# 6. Deploy the whole stack. Idempotent: containers whose image/config changed
#    are recreated; unchanged services (db, redis, minio, glitchtip) stay up.
#    First boot also creates the new services/networks (pgbouncer, celery, …).
log "Starting services..."
docker compose up -d

# 6b. Reload nginx so bind-mounted nginx.conf changes take effect
log "Reloading nginx (proxy config)..."
docker compose restart nginx

# 7. Health check
log "Running health check..."
if wait_for_health; then
    ok "Deploy successful — app is healthy"

    # Update rollback tag to current image for next time
    NEW_IMAGE=$(docker compose images -q app 2>/dev/null | head -1)
    if [ -n "$NEW_IMAGE" ]; then
        docker tag "$NEW_IMAGE" "$ROLLBACK_IMAGE"
    fi

    # Cleanup old images
    docker image prune -f > /dev/null 2>&1 || true

    COMMIT=$(git log --oneline -1)
    ok "Version: $COMMIT"
    cleanup_rollback_file
    exit 0
else
    # Health check failed — rollback
    rollback
    cleanup_rollback_file
    fail "Deploy failed — rolled back to previous version"
    docker compose logs --tail=30 app
    exit 1
fi
