# 💫 Bondi

A modern Persian-language dating app for the Iranian market. Free to use, with optional premium subscription to remove ads.

- **Repo:** `dating-app` (internal app name: **Bondi**)
- **Backend-only repo** — the Flutter client lives separately.

## Features

- 🔍 Location-based profile discovery
- ❤️ Swipe to like or pass
- 💬 Real-time chat with matches
- 📸 Photo verification & moderation
- ✅ Identity verification badge
- 🎁 Rewards for watching ads and leaving reviews
- 👑 Premium subscription (ad-free experience)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| Database | PostgreSQL + PostGIS |
| Cache / Realtime | Redis |
| File storage | MinIO (S3-compatible) |
| Error Tracking | Bugsink (self-hosted, Sentry-compatible) |
| Task Queue | Celery |
| Mobile | Flutter |
| Containers | Docker + Docker Compose |

## Quick Start (Ubuntu / Debian)

```bash
# 1. Clone the repository
git clone https://github.com/EhsanRezaie/dating-app.git
cd dating-app

# 2. Install uv (fast Python package/venv manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create the virtualenv + install dependencies (incl. dev tools)
uv sync --all-groups

# 4. Copy environment file and fill in your values
cp .env.example .env

# 5. Start the infrastructure (Postgres, Redis, MinIO) via Docker
docker compose up -d db redis minio

# 6. Run migrations
alembic upgrade head

# 7. Seed reference data (interests)
python -m app.db.scripts.seed_interests

# 8. Run the dev server
uvicorn app.main:app --reload
```

> Full setup guides for **macOS / Windows**, the complete Docker services reference,
> Bugsink error tracking, and the testing strategy are in **[docs/WIKI.md](docs/WIKI.md)** (Part C).

## Useful Commands

```bash
# Dev server (local)
uvicorn app.main:app --reload

# Docker (full stack — app + db + redis + minio + bondi_bugsink)
docker compose up -d

# Docker (dev with hot-reload, source mounted)
docker compose up -d app

# Tests (requires test infra — see below)
pytest tests/ -v                    # all tests
pytest tests/test_auth.py -v        # single file

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Seed data (safe to re-run)
python -m app.db.scripts.seed_interests
python -m app.db.scripts.seed_dummy_users
```

## Tests

Tests need the test infrastructure running first:

```bash
docker compose -f docker-compose.test.yml up -d
```

Ports: Postgres **5433**, Redis **6380**, MinIO **9090**. `conftest.py` auto-loads
`.env.test` with `override=True` — no manual env setup needed. Per-file test status
and the full testing strategy: [docs/WIKI.md](docs/WIKI.md) (Part C).

## Docs

| Document | Contents |
|----------|----------|
| [docs/WIKI.md](docs/WIKI.md) | Full technical reference: architecture, DB schema, API, WebSocket protocol, business rules, testing, current hardening state |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Zero-to-hero production deployment: VPS → SSL → backups → troubleshooting |
| [docs/TODO.md](docs/TODO.md) | Master task list: everything shipped ✅ and everything remaining ⬜ |
| [AGENTS.md](AGENTS.md) | Conventions for coding agents working on this repo |

## Production

Production deployment is a single, complete guide: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.
Migrations are **committed to git** — deploy only runs `alembic upgrade head`; never
autogenerate in production.

## Admin Access

Admin endpoints use the `X-Admin-Key` header (value from `settings.ADMIN_SECRET_KEY`).
Test admin: `admin@test.com` / `admin123`. Secure remote admin access via WireGuard VPN:
see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (Admin VPN section).
