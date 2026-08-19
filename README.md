# 💫 Dating App

A modern Persian-language dating app for the Iranian market. Free to use, with optional premium subscription to remove ads.

---

## Features

- 🔍 Location-based profile discovery
- ❤️ Swipe to like or pass
- 💬 Real-time chat with matches
- 📸 Photo verification & moderation
- ✅ Identity verification badge
- 🎁 Rewards for watching ads and leaving reviews
- 👑 Premium subscription (ad-free experience)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| Database | PostgreSQL + PostGIS |
| Cache / Realtime | Redis |
| File storage | MinIO (S3-compatible) |
| Error Tracking | GlitchTip (self-hosted, Sentry-compatible) |
| Task Queue | Celery |
| Mobile | Flutter |
| Containers | Docker + Docker Compose |

---

## Prerequisites

Make sure you have these installed before starting:

| Tool | Version | Download |
|------|---------|----------|
| Python | 3.11+ | https://python.org |
| Docker | Latest | https://docker.com |
| Docker Compose | v2+ | included with Docker Desktop |
| Git | Latest | https://git-scm.com |

---

## Setup Guide

### 🐧 Ubuntu / Debian

```bash
# 1. Clone the repository
git clone https://github.com/EhsanRezaie/dating-app.git
cd dating-app

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Copy environment file and fill in your values
cp .env.example .env
nano .env

# 5. Install Docker (skip if already installed)
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker

# 6. Start database, Redis, and MinIO
docker compose up -d

# Verify MinIO buckets were created (should show "MinIO buckets ready")
docker compose logs minio-init

# 7. Run database migrations
alembic upgrade head

# 8. Seed reference data (interests, etc.)
python -m app.db.scripts.seed_interests

# 9. Start the development server
uvicorn app.main:app --reload
```

---

### 🍎 macOS

```bash
# 1. Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install Python
brew install python@3.11

# 3. Clone the repository
git clone https://github.com/EhsanRezaie/dating-app.git
cd dating-app

# 4. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 5. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 6. Copy environment file and fill in your values
cp .env.example .env
nano .env

# 7. Install Docker Desktop for Mac
# Download from: https://www.docker.com/products/docker-desktop/
# After installing, open Docker Desktop and wait for it to start

# 8. Start database, Redis, and MinIO
docker compose up -d

# Verify MinIO buckets were created (should show "MinIO buckets ready")
docker compose logs minio-init

# 9. Run database migrations
alembic upgrade head

# 10. Seed reference data (interests, etc.)
python -m app.db.scripts.seed_interests

# 11. Start the development server
uvicorn app.main:app --reload
```

---

### 🪟 Windows

> **Recommended:** Use WSL2 (Windows Subsystem for Linux) for the best experience.
> Follow the Ubuntu guide inside WSL2.

**Native Windows setup:**

```powershell
# 1. Install Python from https://python.org (check "Add to PATH" during install)

# 2. Install Git from https://git-scm.com

# 3. Clone the repository (in PowerShell or Git Bash)
git clone https://github.com/EhsanRezaie/dating-app.git
cd dating-app

# 4. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate

# 5. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 6. Copy environment file and fill in your values
copy .env.example .env
notepad .env

# 7. Install Docker Desktop for Windows
# Download from: https://www.docker.com/products/docker-desktop/
# Enable WSL2 backend during installation

# 8. Start database, Redis, and MinIO (in PowerShell with Docker running)
docker compose up -d

# Verify MinIO buckets were created (should show "MinIO buckets ready")
docker compose logs minio-init

# 9. Run database migrations
alembic upgrade head

# 10. Seed reference data (interests, etc.)
python -m app.db.scripts.seed_interests

# 11. Start the development server
uvicorn app.main:app --reload
```

---

## Verify Setup

After starting the server, open your browser:

- **Health check:** http://localhost:8000/health → should return `{"status": "ok"}`
- **API docs (Swagger):** http://localhost:8000/api/docs
- **API docs (ReDoc):** http://localhost:8000/api/redoc
- **OpenAPI JSON:** http://localhost:8000/api/openapi.json
- **MinIO console:** http://localhost:9001 (login `minioadmin` / `minioadmin`) → browse uploaded photos, confirm `photos-public` and `photos-private` buckets exist
- **GlitchTip dashboard:** http://localhost:8080 (login `admin@bondi_glitchtip.dev` / `admin123`) → error tracking dashboard

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```env
# Database
DATABASE_URL=postgresql+asyncpg://bondi_admin:CHANGE_ME@localhost:5432/bondi

# Redis
REDIS_URL=redis://localhost:6379

# Security — change this to a long random string in production
SECRET_KEY=your-secret-key-here

# JWT
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# Admin panel access
ADMIN_SECRET_KEY=your-admin-key-here

# Google OAuth (get from https://console.cloud.google.com)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# MinIO / S3-compatible object storage
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=us-east-1
S3_PUBLIC_BUCKET=photos-public
S3_PRIVATE_BUCKET=photos-private
S3_PUBLIC_BASE_URL=http://localhost:9000/photos-public
S3_SIGNED_URL_EXPIRE_SECONDS=900

# App
APP_NAME=DatingApp
DEBUG=True
```

---

## Running Tests

Tests require the test infrastructure (Postgres, Redis, **and MinIO**) running first:

```bash
docker compose -f docker-compose.test.yml up -d

# Confirm MinIO test buckets were created (should show "Test MinIO buckets ready")
docker compose -f docker-compose.test.yml logs minio-test-init
```

Then:

```bash
# Make sure venv is activated
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/

# Run a specific test file
pytest tests/test_auth.py -v
```

> Tests run against an isolated database that is created fresh and destroyed after each run.

> **Current status:** `test_auth.py`, `test_users.py`, and `test_photos.py` are verified passing against the MinIO-based setup. Other test files haven't been re-run since the MinIO migration and should be re-verified — see `dev.md` § Testing Strategy for the full per-file status.

---

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| **Nginx** | **80** | **Reverse proxy (entry point)** |
| **App (FastAPI)** | 8000 (internal) | API server |
| PostgreSQL + PostGIS | 5432 | Main database |
| Redis | 6379 | Cache + realtime |
| MinIO | 9000 (API), 9001 (console) | Photo storage (S3-compatible) |
| GlitchTip (web) | 8080 | Error tracking dashboard |
| GlitchTip (worker) | — | Event ingestion + processing |

### Start everything

```bash
docker compose up -d
```

App is available at `http://localhost` (via Nginx). The entrypoint auto-runs migrations + seeds on first start.

### Dev mode (hot-reload)

Set `ENVIRONMENT=development` in your `.env` file. The entrypoint detects this and adds `--reload` automatically.

### Stop services

```bash
docker compose down
```

### View logs

```bash
docker compose logs -f          # all services
docker compose logs -f app      # app only
```

### Reset everything

WARNING: deletes all data, including uploaded photos.

```bash
docker compose down -v
docker compose up -d
```

---

## GlitchTip Error Tracking Setup

[GlitchTip](https://bondi_glitchtip.com/) is a self-hosted, open-source error tracker — a drop-in Sentry alternative. It catches unhandled exceptions and errors from the FastAPI app and displays them in a web dashboard.

### How It Works

- **GlitchTip (web)** — receives error events from the app via the Sentry SDK protocol, serves the dashboard UI
- **GlitchTip (worker)** — processes queued events and writes them to the database (required — without it, events are silently dropped)
- **sentry-sdk** — the Python client in `app/main.py` auto-captures exceptions and sends them to GlitchTip

### Architecture

```
FastAPI App ──sentry_sdk──▸ GlitchTip Web (:8080) ──queue──▸ GlitchTip Worker ──▸ PostgreSQL
                                                                              └──▸ Redis (queue backend)
```

### Prerequisites

No additional dependencies. GlitchTip runs in Docker (included in `docker-compose.yml`). The Python `sentry-sdk[fastapi]` package is already in `requirements.txt`.

### Platform Setup

GlitchTip setup is the **same on all platforms** — it runs entirely in Docker. The only prerequisite is Docker + Docker Compose installed and running.

#### Linux (Ubuntu/Debian)

```bash
# Docker is already installed from the main setup guide.
# GlitchTip starts automatically with the rest of the stack:

docker compose up -d

# Verify all GlitchTip services are running:
docker ps --filter "name=bondi_bondi_glitchtip"
# Should show:
#   bondi_bondi_glitchtip        Up   (web dashboard)
#   bondi_bondi_glitchtip_worker Up   (event worker)

# Wait ~15 seconds for migrations, then open:
# Dashboard: http://localhost:8080
```

#### macOS

```bash
# Same commands — Docker Desktop handles the Linux containers:

docker compose up -d

# Verify:
docker ps --filter "name=bondi_bondi_glitchtip"
# Should show both bondi_bondi_glitchtip and bondi_bondi_glitchtip_worker

# Open: http://localhost:8080
```

#### Windows (WSL2 or PowerShell)

```powershell
# Same commands — Docker Desktop handles the Linux containers:

docker compose up -d

# Verify:
docker ps --filter "name=bondi_bondi_glitchtip"

# Open: http://localhost:8080
```

#### Server Deployment (Linux VPS / Cloud)

```bash
# 1. Clone and start everything
git clone <repo-url> && cd project-d
docker compose up -d

# 2. Wait for GlitchTip to initialize (~15 seconds)
sleep 15

# 3. Create admin user
docker exec bondi_bondi_glitchtip python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.create_superuser(email='admin@yourdomain.com', password='CHANGEME')
print(f'Admin created: {user.email}')
"

# 4. Create organization + project + get DSN
docker exec bondi_bondi_glitchtip python manage.py shell -c "
from django.apps import apps
from django.contrib.auth import get_user_model

User = get_user_model()
OrgModel = apps.get_model('organizations_ext', 'Organization')
OrgUser = apps.get_model('organizations_ext', 'OrganizationUser')
OrgOwner = apps.get_model('organizations_ext', 'OrganizationOwner')
ProjectModel = apps.get_model('projects', 'Project')
KeyModel = apps.get_model('projects', 'ProjectKey')

user = User.objects.get(email='admin@yourdomain.com')
org = OrgModel.objects.create(name='YourApp', slug='yourapp')
org_user = OrgUser.objects.create(user=user, organization=org, role=0)
OrgOwner.objects.create(organization_user=org_user, organization=org)
project = ProjectModel.objects.create(name='YourApp', slug='yourapp', organization=org, platform='python')
key = KeyModel.objects.create(project=project, name='Default')
print(f'DSN: {key.get_dsn()}')
"

# 5. Update .env with the DSN (replace YOUR_SERVER_IP with your actual IP)
#    GLITCHTIP_DSN=http://<public_key>@YOUR_SERVER_IP:8080/1

# 6. Update SECRET_KEY for GlitchTip (generate a random one)
#    GLITCHTIP_SECRET_KEY=$(openssl rand -hex 32)
```

### First-Time GlitchTip Setup (All Platforms)

After `docker compose up -d`, GlitchTip needs a one-time initialization:

```bash
# 1. Wait for the web container to be healthy
sleep 15

# 2. Create admin user
docker exec bondi_bondi_glitchtip python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.create_superuser(email='admin@bondi_glitchtip.dev', password='admin123')
print('Admin created successfully')
"

# 3. Create organization, project, and API key
docker exec bondi_bondi_glitchtip python manage.py shell -c "
from django.apps import apps
from django.contrib.auth import get_user_model

User = get_user_model()
OrgModel = apps.get_model('organizations_ext', 'Organization')
OrgUser = apps.get_model('organizations_ext', 'OrganizationUser')
OrgOwner = apps.get_model('organizations_ext', 'OrganizationOwner')
ProjectModel = apps.get_model('projects', 'Project')
KeyModel = apps.get_model('projects', 'ProjectKey')

user = User.objects.get(email='admin@bondi_glitchtip.dev')
org = OrgModel.objects.create(name='DatingApp', slug='datingapp')
org_user = OrgUser.objects.create(user=user, organization=org, role=0)
OrgOwner.objects.create(organization_user=org_user, organization=org)
project = ProjectModel.objects.create(name='DatingApp', slug='datingapp', organization=org, platform='python')
key = KeyModel.objects.create(project=project, name='Default')
print(f'DSN: {key.get_dsn()}')
"

# 4. Copy the DSN from the output above and add it to your .env file:
#    GLITCHTIP_DSN=http://<public_key>@localhost:8080/1

# 5. Restart the FastAPI app to pick up the new DSN
```

### Configuration (.env)

```env
# GlitchTip DSN — get this from the dashboard after first-time setup
# Format: http://<public_key>@<host>:<port>/<project_id>
GLITCHTIP_DSN=http://56b584bd1e1443a6a14db49671e2f5fe@localhost:8080/1

# GlitchTip secret key — generate with: openssl rand -hex 32
GLITCHTIP_SECRET_KEY=C6bqhRHE_hXolh2Zm35WnOgO9TDy0DQqGvMcUBYSGAE
```

### Verifying It Works

```bash
# 1. Start the FastAPI app
uvicorn app.main:app --reload

# 2. Open the GlitchTip dashboard
#    http://localhost:8080
#    Login: admin@bondi_glitchtip.dev / admin123

# 3. Send a test error from a separate terminal:
source venv/bin/activate
python -c "
import sentry_sdk
sentry_sdk.init(dsn='YOUR_DSN_HERE', environment='development')
try:
    1 / 0
except Exception:
    sentry_sdk.capture_exception()
    print('Test error sent!')
sentry_sdk.flush()
"

# 4. Check the dashboard — you should see a ZeroDivisionError appear within seconds
```

### How Errors Are Captured

The FastAPI app initializes `sentry_sdk` in `app/main.py` on startup:

```python
if settings.GLITCHTIP_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=settings.GLITCHTIP_DSN,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
    )
```

This automatically captures:
- Unhandled exceptions in FastAPI endpoints
- SQLAlchemy query errors
- Request/response performance traces (10% sample)

To manually capture errors or messages anywhere in the code:

```python
import sentry_sdk

# Capture an exception
sentry_sdk.capture_exception()

# Capture a custom message
sentry_sdk.capture_message("Something important happened", level="warning")

# Add context for debugging
with sentry_sdk.push_scope() as scope:
    scope.set_tag("user_id", user.id)
    scope.set_extra("endpoint", "/api/v1/discover")
    sentry_sdk.capture_exception()
```

### Docker Compose Services Reference

| Container | Service | Purpose |
|-----------|---------|---------|
| `bondi_bondi_glitchtip` | `bondi_glitchtip` | Web UI + event ingestion API (port 8080) |
| `bondi_bondi_glitchtip_worker` | `bondi_glitchtip-worker` | Background worker that processes events |
| `bondi_bondi_glitchtip_db_init` | `bondi_glitchtip-db-init` | One-shot: creates the `bondi_glitchtip` database |

### Troubleshooting

**500 error on `/api/0/users/me/` after login:**
- Caused by using a `.local` email domain (e.g. `admin@bondi_glitchtip.local`) — Pydantic rejects `.local` as a reserved TLD
- Fix: use a real domain like `admin@bondi_glitchtip.dev` or `admin@yourdomain.com`
- If already created: `docker exec bondi_bondi_glitchtip python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.get(is_superuser=True); u.email = 'admin@bondi_glitchtip.dev'; u.save()"`

**Events not showing up in dashboard:**
- Check the worker is running: `docker ps --filter "name=bondi_bondi_glitchtip_worker"`
- If missing, the `bondi_glitchtip-worker` service was added after initial setup. Run: `docker compose up -d bondi_glitchtip-worker`
- Worker logs: `docker logs bondi_bondi_glitchtip_worker`

**Dashboard shows "Mode: Web only":**
- This means the worker container is not running. The web container always shows "Web only" — the worker is separate.
- Fix: `docker compose up -d bondi_glitchtip-worker`

**GlitchTip won't start / database errors:**
- Recreate from scratch: `docker compose down -v && docker compose up -d`
- Wait 15 seconds for initialization, then run the First-Time GlitchTip Setup above

**`sentry_sdk` import error on app startup:**
- Install the missing dependency: `pip install sentry-sdk[fastapi]`
- If you see `jinja2 must be installed`: `pip install jinja2`

**Port 8080 already in use:**
- Change the mapping in `docker-compose.yml`:
  ```yaml
  bondi_glitchtip:
    ports:
      - "8180:80"   # use port 8180 instead
  ```
- Update `GLITCHTIP_DSN` in `.env` to use the new port

### Production / Server Deployment Notes

- Change the GlitchTip admin password immediately after first setup
- Set a strong `GLITCHTIP_SECRET_KEY` (generate with `openssl rand -hex 32`)
- Restrict `ALLOWED_HOSTS` on the GlitchTip container in production:
  ```yaml
  bondi_glitchtip:
    environment:
      ALLOWED_HOSTS: yourdomain.com
  ```
- Set `traces_sample_rate` to `0.0` in production to disable performance tracing (or keep `0.1` for 10% sampling)
- GlitchTip retains events for 90 days by default (configurable)

---

## Seeding Reference Data

Static reference tables (e.g. `interests`) are populated from JSON files under `app/db/seed_data/` using idempotent seed scripts in `app/db/scripts/`. Safe to re-run anytime — existing rows are updated in place, new rows are inserted, nothing is duplicated or deleted.

```bash
# Seed / update interests from app/db/seed_data/interests.json
python -m app.db.scripts.seed_interests

# Seed 1000 dummy users (test1@test.com … test1000@test.com, password: 12345678)
python -m app.db.scripts.seed_dummy_users
```

Run these after migrations on first setup. Interests can be re-run any time `interests.json` is edited. Dummy users can be re-run safely — existing `%@test.com` users are deleted first.

---

## Project Structure

```
dating-app/
├── app/
│   ├── api/v1/endpoints/   # Route handlers
│   ├── core/               # Config, security, dependencies
│   ├── db/                 # Database engine, session, seed data & scripts
│   │   ├── seed_data/      # JSON reference data (interests, prompts, dummy_users)
│   │   └── scripts/        # Idempotent seed/sync scripts
│   ├── models/             # SQLAlchemy models
│   ├── schemas/            # Pydantic schemas (request/response)
│   ├── services/           # Business logic
│   ├── tasks/              # Celery async tasks
│   └── main.py             # FastAPI app entry point
├── alembic/                # Database migrations
├── tests/                  # Unit and integration tests
├── docs/                   # Developer documentation
├── scripts/                # Deploy, backup, restore, firewall
├── nginx/nginx.conf        # Reverse proxy config
├── docker-compose.yml      # App + all infrastructure
├── docker-compose.test.yml # Test infrastructure
├── Dockerfile
├── entrypoint.sh           # Auto-migration + seeding on startup
├── .env.example
├── requirements.txt
└── README.md
```

---

## Production Deployment (VPS)

### First-time setup

```bash
# 1. Clone repo on your VPS
git clone <repo-url> && cd dating-app

# 2. Configure firewall
sudo bash firewall.sh

# 3. Create production env
cp .env.example .env
nano .env   # fill in real secrets (SECRET_KEY, ENCRYPTION_SECRET, etc.)

# 4. Deploy
bash scripts/deploy.sh
```

App is available at `http://<your-server-ip>`

### Subsequent deploys

```bash
git pull
bash scripts/deploy.sh
```

### Database backups

```bash
# Manual backup
bash scripts/backup.sh

# Automated (daily at 2am)
echo "0 2 * * * bash /opt/dating-app/scripts/backup.sh" | crontab -
```

### Restore from backup

```bash
bash scripts/restore.sh /opt/dating-app/backups/db_20250101_020000.sql.gz
```

### Add SSL (when you have a domain)

```bash
# 1. Point DNS to your server IP

# 2. Install certbot
apt install certbot

# 3. Get certificate
certbot certonly --standalone -d api.yourdomain.com

# 4. Copy certs
cp /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem nginx/certs/
cp /etc/letsencrypt/live/api.yourdomain.com/privkey.pem nginx/certs/

# 5. Uncomment SSL block in nginx/nginx.conf

# 6. Restart nginx
docker compose restart nginx
```

---

## Contributing

This is a private project. For access, contact the project owner.

---

## License

Private — All rights reserved.