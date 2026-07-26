# Server Setup Guide

Full deployment guide for the dating app on Ubuntu 24.04 LTS with Docker.

---

## Quick Start

```bash
# 1. SSH into server
ssh root@YOUR_SERVER_IP

# 2. Install Docker
apt update && apt upgrade -y
apt install -y git curl wget ufw
apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. Configure firewall
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP"
ufw allow 8080/tcp comment "GlitchTip"
ufw --force enable

# 4. Clone and deploy
cd /opt
git clone https://github.com/EhsanRezaie/project_d.git demo-bondi
cd demo-bondi

# 5. Create .env
cp .env.example .env
nano .env   # fill in secrets (see below)

# 6. Start everything
docker compose up -d --build
docker compose logs -f   # wait for "Application startup complete"

# 7. Setup GlitchTip
sleep 15
docker exec dating_glitchtip python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.create_superuser(email='admin@glitchtip.dev', password='admin123')
print('Admin created')
"
docker exec dating_glitchtip python manage.py shell -c "
from django.apps import apps
from django.contrib.auth import get_user_model
User = get_user_model()
OrgModel = apps.get_model('organizations_ext', 'Organization')
OrgUser = apps.get_model('organizations_ext', 'OrganizationUser')
OrgOwner = apps.get_model('organizations_ext', 'OrganizationOwner')
ProjectModel = apps.get_model('projects', 'Project')
KeyModel = apps.get_model('projects', 'ProjectKey')
user = User.objects.get(email='admin@glitchtip.dev')
org = OrgModel.objects.create(name='DatingApp', slug='datingapp')
org_user = OrgUser.objects.create(user=user, organization=org, role=0)
OrgOwner.objects.create(organization_user=org_user, organization=org)
project = ProjectModel.objects.create(name='DatingApp', slug='datingapp', organization=org, platform='python')
key = KeyModel.objects.create(project=project, name='Default')
print(f'DSN: {key.get_dsn()}')
"

# 8. Add GlitchTip DSN to .env
nano .env
# Set: GLITCHTIP_DSN=http://<public_key>@glitchtip:80/2
docker compose restart app
```

---

## .env Secrets

Generate these before editing `.env`:

```bash
openssl rand -hex 32   # SECRET_KEY
openssl rand -hex 16   # ENCRYPTION_SECRET
openssl rand -hex 16   # ADMIN_SECRET_KEY
openssl rand -hex 32   # GLITCHTIP_SECRET_KEY
```

Required `.env` values:

```env
DATABASE_URL=postgresql+asyncpg://dating_user:dating_pass@localhost:5432/dating_db
REDIS_URL=redis://localhost:6379
SECRET_KEY=<openssl rand -hex 32>
ADMIN_SECRET_KEY=<openssl rand -hex 16>
ENCRYPTION_SECRET=<openssl rand -hex 16>
GLITCHTIP_SECRET_KEY=<openssl rand -hex 32>
GLITCHTIP_DSN=http://<public_key>@glitchtip:80/2
ENVIRONMENT=production
DEBUG=False
CORS_ORIGINS=*
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_PUBLIC_BUCKET=photos-public
S3_PRIVATE_BUCKET=photos-private
S3_PUBLIC_BASE_URL=http://localhost:9000/photos-public
FCM_SERVICE_ACCOUNT_PATH=
ZARINPAL_MERCHANT_ID=
ZARINPAL_SANDBOX=true
NSFW_ENABLED=true
NSFW_THRESHOLD=0.8
```

---

## Docker Services

| Container | Port | Purpose |
|-----------|------|---------|
| Nginx | 80 (exposed) | Reverse proxy |
| App (FastAPI) | 8000 (internal) | API server |
| PostgreSQL | 5432 (internal) | Database |
| Redis | 6379 (internal) | Cache + realtime |
| MinIO | 9000 (internal) | Photo storage |
| MinIO Console | 9001 (internal) | MinIO web UI |
| GlitchTip | 8080 (exposed) | Error tracking |
| GlitchTip Worker | — | Event processing |

---

## Verification

```bash
# Health check
curl http://localhost/health
# Expected: {"status":"ok"}

# Swagger docs (temporary — requires ENVIRONMENT=development)
sed -i 's/ENVIRONMENT=production/ENVIRONMENT=development/' .env
docker compose restart app
# Open: http://YOUR_SERVER_IP/api/docs
sed -i 's/ENVIRONMENT=development/ENVIRONMENT=production/' .env
docker compose restart app

# GlitchTip dashboard
# Open: http://YOUR_SERVER_IP:8080
# Login: admin@glitchtip.dev / admin123

# Test GlitchTip error reporting
docker exec dating_app python -c "
import sentry_sdk
sentry_sdk.init(dsn='$(grep GLITCHTIP_DSN .env | cut -d= -f2-)')
try:
    1 / 0
except Exception:
    sentry_sdk.capture_exception()
    print('Test error sent!')
sentry_sdk.flush()
"
```

---

## Seed Data

```bash
# 158 interests (safe to re-run)
docker exec dating_app python -m app.db.scripts.seed_interests

# 1000 dummy users (test1@test.com ... test1000@test.com, password: 12345678)
docker exec dating_app python -m app.db.scripts.seed_dummy_users
```

---

## Useful Commands

```bash
# View logs
docker compose logs -f app
docker compose logs -f glitchtip

# Restart a service
docker compose restart app

# Rebuild after code changes
git pull
docker compose up -d --build

# Stop everything
docker compose down

# Stop and remove all data (WARNING: deletes database)
docker compose down -v

# Access PostgreSQL
docker exec -it dating_db psql -U dating_user -d dating_db

# Run migrations manually
docker exec dating_app alembic upgrade head

# Check firewall
ufw status
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| App won't start (libxcb error) | Already fixed in Dockerfile — rebuild: `docker compose up -d --build` |
| GlitchTip worker crash | Already fixed — worker runs migrations before starting |
| 502 Bad Gateway | `docker compose logs app` — check if app is running |
| `/api/docs` returns 404 | Set `ENVIRONMENT=development` in .env and restart |
| GlitchTip can't login | Create superuser (see Quick Start step 7) |
| GlitchTip "database does not exist" | Worker runs migrate automatically — just wait and restart |
| Port 8080 refused | `ufw allow 8080/tcp` + check glitchtip service has `ports: ["8080:80"]` |
