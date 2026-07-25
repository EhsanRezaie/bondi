# AGENTS.md

Persian-language dating app (FastAPI + PostgreSQL/PostGIS + Redis + MinIO + Celery + Flutter). Backend-only repo.

## Quick commands

```bash
# Dev server (local)
uvicorn app.main:app --reload

# Docker (full stack — app + db + redis + minio + glitchtip)
docker compose up -d

# Docker (dev with hot-reload, source mounted)
docker compose up -d app

# Tests (requires test infra — see below)
pytest tests/done/ -v                    # all tests
pytest tests/done/test_auth.py -v        # single file

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Seed data (safe to re-run)
python -m app.db.scripts.seed_interests
python -m app.db.scripts.seed_dummy_users
```

## Docker

`docker compose up -d` starts everything: Nginx (80), app, Postgres (5432), Redis (6379), MinIO (9000/9001), GlitchTip (8080). The `entrypoint.sh` auto-detects `ENVIRONMENT` — adds `--reload` if development, plain uvicorn if production.

Inside containers, DATABASE_URL/REDIS_URL/S3_ENDPOINT_URL are overridden to use Docker service names (`db`, `redis`, `minio`) instead of `localhost`.

## Test infra prerequisite

Tests need `docker compose -f docker-compose.test.yml up -d` running first. Ports: Postgres 5433, Redis 6380, MinIO 9090. `conftest.py` auto-loads `.env.test` with `override=True` — no manual env setup needed.

## Admin auth

Admin endpoints use `X-Admin-Key` header (value from `settings.ADMIN_SECRET_KEY`). Test admin: `admin@test.com` / `admin123`.

## Swagger/ReDoc

Docs at `/api/docs` and `/api/redoc` are only served when `ENVIRONMENT=development` (`app/main.py:67-69`).

## App name

Internal app name is "Bondi" (`app/core/config.py:10`), not "DatingApp" as some env files suggest.

## MinIO naming constraint

MinIO service names **must** use hyphens (`minio-test`), never underscores (`minio_test`). MinIO's S3 hostname validation rejects underscores with "Invalid Request (invalid hostname)". This applies to both `docker-compose.yml` and `docker-compose.test.yml`.

## Compose filename

The test compose file is `docker-compose.test.yml` (dot), not `docker-compose_test.yml` (underscore) as README states.

## Welcome premium bonus

`auth.py:301-302` grants `settings.WELCOME_BONUS_DAYS` (7, via `.env`) of premium to every new user when they complete onboarding. This means `likes_remaining_today` is `null` (unlimited) for all freshly-registered test users.

## Test fixtures

| Fixture | Behavior |
|---------|----------|
| `setup_database` (session) | Drops all tables, recreates them, seeds interests from `app/db/seed_data/interests.json`, creates admin user `admin@test.com`/`admin123` with `onboarding_complete` and `is_verified=true` |
| `reset_state` (per-test) | Deletes non-admin users + their profiles/settings, truncates all other tables, re-seeds interests, flushes Redis |
| `patch_redis` (per-test) | Swaps `app.core.redis.redis_client` with a test instance, restores original |
| `disable_rate_limiting` | Sets `limiter._enabled = False` |
| `mock_websocket_manager` | Patches `app.api.v1.endpoints.swipes.websocket_manager` |
| `mock_email_service` | Patches `app.services.email_service.send_verification_code` |

## Encryption

Message keys derived on-the-fly from `match_id + ENCRYPTION_SECRET` (PBKDF2, 100K iterations). Keys are **never stored**. SQLAlchemy `message.content` property auto-encrypts on set, auto-decrypts on get.

## Photo storage

`Photo.url` stores only the object key (e.g. `users/{id}/{photo_id}.jpg`). `PhotoService.get_photo_url()` resolves the full URL at read time based on moderation status.

## pytest

`pytest.ini` sets only `asyncio_mode = auto`. No `@pytest.mark.asyncio` decorators needed.

## Imports

Most `app/` package `__init__.py` files are empty — no barrel imports. Import directly from the module.

## Face verification

`face_verification_service.py` is a singleton using InsightFace buffalo_l model. Model is lazy-loaded on first request (thread-safe via `threading.Lock`). All CPU-bound work (OpenCV decode, InsightFace inference) runs in `asyncio.run_in_executor` to avoid blocking the event loop.

Challenge state lives in Redis (`verify_challenge:{user_id}`, 10-min TTL). Cooldowns (`verify_cooldown:{user_id}`, 24h TTL) and daily attempt counters (`verify_attempts:{user_id}:{date}`, 24h TTL) are also Redis-only.

`_AUTO_FACE_VERIFY = False` in admin_photos.py — the temporary bypass is disabled. Photo uploads no longer auto-set `face_verified=True`.

Test endpoint: `POST /admin/face-verification/test` accepts a video + user_id, runs the full pipeline, returns per-step debug JSON without modifying any DB records.

## Face verification tests

`tests/done/test_face_verification.py` has 28 tests covering challenge generation, verification status, video submission (with mocked face service), and pure unit tests for the service layer. All InsightFace/OpenCV calls are mocked in endpoint tests; only the service unit tests run real numpy/cosine comparisons.
