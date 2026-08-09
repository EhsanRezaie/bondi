# Deployment Operations Guide

> 📖 **The canonical, up-to-date guide lives at [`docs/DEPLOYMENT.md`](DEPLOYMENT.md).**
> This file covers only the secret/password flows; specifics like first-boot,
> Celery, metrics and rollback are in the canonical guide.

Companion to `server_setup.md`. Covers the secret configuration that protects
Redis, MinIO and PostgreSQL when running the `docker compose` stack.

---

## Minimum `.env` secrets (safe production values)

```env
# --- PostgreSQL (used to create the DB + used by pgbouncer) ---
POSTGRES_USER=dating_user
POSTGRES_PASSWORD=<openssl rand -hex 16>
POSTGRES_DB=dating_db

# --- Redis password (empty = no auth; set one in production) ---
REDIS_PASSWORD=<openssl rand -hex 24>

# --- MinIO root credentials ---
MINIO_ROOT_USER=<openssl rand -hex 12>
MINIO_ROOT_PASSWORD=<openssl rand -hex 24>
```

When running locally without Docker, `REDIS_PASSWORD` can stay empty and
`REDIS_URL` is `redis://localhost:6379`.

### How the password flows

1. `docker-compose.yml` starts redis with
   `redis-server --requirepass ${REDIS_PASSWORD:-} --appendonly yes`.
2. The app service sets `REDIS_URL: redis://:${REDIS_PASSWORD:-}@redis:6379`,
   so the app authenticates automatically.
3. GlitchTip and its worker use
   `redis://:${REDIS_PASSWORD:-}@redis:6379/1` (DB 1, kept separate).
4. `docker-compose.test.yml` and local `pytest` use `REDIS_URL` directly —
   unauthenticated, which is fine for CI.

> If you change `REDIS_PASSWORD` after the stack is up, restart redis and the
> app: `docker compose up -d redis app`.

### MinIO credentials

* The `minio` container sets `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`.
* `S3_ACCESS_KEY` / `S3_SECRET_KEY` in `.env` must match them (default
  `minioadmin`). Keep them in sync when rotated.
* `minio-init` uses the same variables to create the buckets
  (`photos-public`, `photos-private`); it must be restarted after a rotation:
  `docker compose up -d minio-init`.

---

## Verify after enabling secrets

```bash
# Compose config is valid and interpolates .env
docker compose config -q

# Redis requires the password
docker exec dating_redis redis-cli -a "$REDIS_PASSWORD" ping   # PONG

# App can reach Redis
docker compose logs app | grep -i redis

# MinIO buckets exist
docker exec dating_minio mc ls local 2>/dev/null || \
  docker exec dating_minio sh -c 'mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD && mc ls local'
```

---

## PgBouncer

The app connects to PostgreSQL through the `pgbouncer` service instead of
directly to `db`. Settings (transaction pooling, 20 default / 5 reserve) live
in `docker-compose.yml`.

Useful checks:

```bash
docker exec dating_pgbouncer sh -c 'echo "SHOW POOLS;" | psql -U dating_user -d pgbouncer'
docker compose logs pgbouncer
```

Migrations still run against `db` directly (DDL is not compatible with
transaction-mode pooling), via the `migrate` service and `entrypoint.sh`.