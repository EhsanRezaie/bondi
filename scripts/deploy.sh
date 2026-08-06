#!/bin/bash
set -e

# ── Config ────────────────────────────────────────────────────────────────────
DEPLOY_DIR="/opt/demo-bondi"
HEALTH_URL="http://localhost/health/ready"
HEALTH_RETRIES=8
HEALTH_INTERVAL=5
ROLLBACK_IMAGE="dating-app:rollback"
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

# 2. Pull latest code
log "Pulling latest code..."
git pull origin main

# 3. Check FCM service account file
if [ ! -f firebase-service-account.json ]; then
    fail "firebase-service-account.json not found — push notifications will be disabled"
    log "Place the file in $DEPLOY_DIR/ or set FCM_SERVICE_ACCOUNT_PATH in .env"
fi

# 4. Login to Docker Hub (if credentials provided)
if [ -n "$DOCKERHUB_USERNAME" ] && [ -n "$DOCKERHUB_TOKEN" ]; then
    log "Logging in to Docker Hub..."
    echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin
fi

# 4. Build/refresh base image (deps) — no-op unless requirements.txt changed
log "Ensuring base image (deps)..."
bash scripts/build-base.sh

# 5. Build new image (thin — skips pip, takes seconds)
log "Building..."
docker compose build app

# 5b. Auto-generate migrations from new code, then apply them (one-shot)
log "Auto-generating + applying migrations..."
docker compose run --rm migrate

# 6. Deploy
log "Starting services..."
docker compose up -d --no-deps app

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
