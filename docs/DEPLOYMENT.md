# Bondi — Deployment Guide (Zero to Hero)

A single, up-to-date guide to take this app from a blank VPS to a running,
monitored, backed-up production stack. **This is the canonical guide** — read it
in order the first time; use the section headers as a checklist thereafter.

> **Stack at a glance (post-hardening — `7b2f942`)**
> Nginx (public) → FastAPI app → PgBouncer → PostgreSQL · Redis (password-auth) ·
> MinIO (S3) · Celery worker + beat · GlitchTip (error tracking) + worker ·
> One-shot migrate service · Prometheus `/metrics` (internal only).

---

## 1. Architecture overview

### 1.1 Component diagram

```
                      ┌───────────────────────────┐
  Browser / App ─────►│  nginx  (ports 80, 443)    │   frontend network (public)
                      └──────┬────────────────────┘
                             │
             ┌───────────────┼───────────────────┐
             │               ▼                   │
             │    ┌─────────────────────┐        │
             │    │  app (FastAPI)      │◄───────┼── health / ready / metrics
             │    │  migrations at boot │        │
             │    └────┬───────────┬────┘        │
             │         │           │             │
             │         ▼           ▼             │
             │  ┌──────────┐  ┌──────────┐       │
             │  │ pgbouncer│  │  redis   │       │
             │  └────┬─────┘  └────┬─────┘       │
             │       │            │              │
             │       ▼            ▼              │
             │  ┌──────────┐  ┌────────────────┐ │
             │  │ postgres │  │ minio (S3)     │ │   internal network (no public IP)
             │  └──────────┘  └────────────────┘ │
             │       ▲                           │
             │       │ (migrate runs DDL here,   │
             │       │  bypassing pgbouncer)     │
             │  ┌──────────┐  ┌───────────┐      │
             │  │ celery-  │  │ celery-   │      │
             │  │ worker   │  │ beat      │      │
             │  └──────────┘  └───────────┘      │
             │  glitchtip (live on :8080)        │
             └───────────────────────────────────┘
```

### 1.2 Networks

| Network | Name | Visibility | Attached to |
|---------|------|-----------|-------------|
| `frontend` | `dating_frontend` | Public bridge | `nginx`, `app`, `celery-worker`, `celery-beat` (outbound egress for FCM) |
| `internal` | `dating_internal` | **internal (no internet)** | db, pgbouncer, redis, minio, minio-init, glitchtip*, migrate, plus nginx/app/workers (media proxy + DB/Redis) |

- The `internal` network is created with `internal: true` — containers on it can
  only reach each other; nothing there is reachable from the host or internet.
- `nginx` sits on **both** networks: public side (`frontend`) serves HTTP, while
  attaching to `internal` is what lets it proxy `/photos-*` to `minio:9000`.
- `celery-worker`/`celery-beat` also join both: `internal` for DB (via pgbouncer)
  + Redis, `frontend` so FCM push calls can reach Google.
- Only `nginx` (80/443) and `glitchtip` (8080) publish host ports.
- **db / redis / minio do NOT publish ports** — never expose them directly.

---

## 2. Provisioning the VPS (from zero)

### 2.1 Buy + DNS

- Recommended: Hetzner CX22 or DigitalOcean droplet, **2 vCPU / 4 GB RAM / 40+ GB SSD**.
- Ubuntu **24.04 LTS**.
- Point a domain (or subdomain) A-record at the server IP, e.g. `api.yourdomain.com` → `1.2.3.4`.

### 2.2 SSH hardening

```bash
# create a non-root, sudo-capable user
adduser deploy
usermod -aG sudo deploy

# add your public key
mkdir -p /home/deploy/.ssh
echo "ssh-ed25519 AAAA... your@email" >> /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys

# tighten sshd
nano /etc/ssh/sshd_config
#   PermitRootLogin no
#   PasswordAuthentication no
systemctl restart sshd
```

> Keep the root session open until the new user can `sudo` in successfully.

### 2.3 Install Docker + Compose

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
| tee /etc/apt/sources.list.d/docker.list > /dev/null

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
docker --version && docker compose version    # verify both
```

---

## 3. First clone + `.env` (critical)

### 3.1 Clone

```bash
mkdir -p /opt && cd /opt
git clone git@github.com:EhsanRezaie/project_d.git demo-bondi && cd demo-bondi
```

### 3.2 Generate all secrets

Only values you must create per environment; the rest come from `.env.example`.

```bash
openssl rand -hex 24    # REDIS_PASSWORD
openssl rand -hex 16    # POSTGRES_PASSWORD
openssl rand -hex 12    # MINIO_ROOT_USER
openssl rand -hex 24    # MINIO_ROOT_PASSWORD
openssl rand -hex 32    # SECRET_KEY
openssl rand -hex 16    # ENCRYPTION_SECRET (must be 32 bytes UTF-8 / or 32 chars)
openssl rand -hex 16    # ADMIN_SECRET_KEY
openssl rand -hex 32    # GLITCHTIP_SECRET_KEY
```

### 3.3 Build the `.env`

```bash
cp .env.example .env && nano .env
```

Set **at minimum** these values (📌 = must change):

```env
# --- Postgres (REPLACED in-compose by pgbouncer URL; used to auth) ---
POSTGRES_USER=dating_user
POSTGRES_PASSWORD=<openssl rand -hex 16>            # 📌
POSTGRES_DB=dating_db

# --- Redis (NOW PASSWORD-PROTECTED) ---
REDIS_PASSWORD=<openssl rand -hex 24>               # 📌
# app URL is auto-built inside compose:
#   redis://:<password>@redis:6379

# --- MinIO / S3 ---
MINIO_ROOT_USER=<openssl rand -hex 12>              # 📌
MINIO_ROOT_PASSWORD=<openssl rand -hex 24>          # 📌
S3_ACCESS_KEY=${MINIO_ROOT_USER:-minioadmin}
S3_SECRET_KEY=${MINIO_ROOT_PASSWORD:-minioadmin}
S3_REGION=us-east-1
S3_PUBLIC_BUCKET=photos-public
S3_PRIVATE_BUCKET=photos-private
# Public host is what clients see — NOT "minio". ⚠️
S3_ENDPOINT_URL=http://api.yourdomain.com
S3_PUBLIC_BASE_URL=http://api.yourdomain.com/photos-public
S3_SIGNED_URL_EXPIRE_SECONDS=900

# --- Security ---
SECRET_KEY=<openssl rand -hex 32>                   # 📌
ENCRYPTION_SECRET=<openssl rand -hex 16>            # 📌 (32 chars)
ADMIN_SECRET_KEY=<openssl rand -hex 16>
ADMIN_USERNAME=admin

# --- App ---
APP_NAME=Bondi
DEBUG=False
ENVIRONMENT=production
CORS_ORIGINS=https://api.yourdomain.com

# --- Celery (durable queue — enable for retries/scheduling) ---
CELERY_ENABLED=true

# --- Firebase push ---
FCM_SERVICE_ACCOUNT_PATH=firebase-service-account.json

# --- GlitchTip ---
GLITCHTIP_SECRET_KEY=<openssl rand -hex 32>
GLITCHTIP_DSN=http://public-key@glitchtip:80/2      # from step 5.6

# --- Payments (only when you get a merchant ID) ---
ZARINPAL_MERCHANT_ID=
ZARINPAL_SANDBOX=true
ZARINPAL_CALLBACK_URL=https://api.yourdomain.com/api/v1/subscriptions/zarinpal/callback
```

> ⚠️ **Do NOT publish `POSTGRES_PASSWORD` unrelated anywhere.** pgbouncer reads
> the same `POSTGRES_*` vars; db and pgbouncer must agree.

### 3.4 Upload the Firebase service account

```bash
# from your local machine:
scp firebase-service-account.json deploy@1.2.3.4:/opt/demo-bondi/
ssh deploy@1.2.3.4 "chmod 600 /opt/demo-bondi/firebase-service-account.json"
```

> The file is gitignored. For CI deploys, put the JSON contents in the
> `FCM_SERVICE_ACCOUNT_JSON` GitHub Actions secret instead.

---

## 4. First boot 🚀 (full stack — not app-only)

> **Good news 🙂** `deploy.sh` now runs full `docker compose up -d` (not
> `--no-deps app`), so it **creates** brand-new services (`pgbouncer`,
> `celery-worker`, `celery-beat`) and the two networks (`dating_frontend`,
> `dating_internal`) on first boot. A one-time manual hand-rolled first boot is
> still shown below for reference:

```bash
cd /opt/demo-bondi
docker compose up -d
```

Watch it come up:

```bash
docker compose ps
docker compose logs -f app
# wait for: "Application startup complete."
```

If you already have an old stack running, **recreate** a container with a
hard-flag so the changed volume/network/pg settings apply:

```bash
docker compose up -d --force-recreate
```

---

## 5. Verify the whole stack

### 5.1 Health endpoints

```bash
# Basic liveness
curl http://localhost/health            # {"status":"ok"}
curl http://localhost/health/ready      # {"status":"ready","db":"ok","redis":"ok"}
```

### 5.2 Redis auth

```bash
docker exec dating_redis redis-cli -a "$REDIS_PASSWORD" ping     # PONG
# without password it must now fail (auth-protected):
docker exec dating_redis redis-cli ping                            # (error)
```

### 5.3 PgBouncer

```bash
# AUTH_TYPE is scram-sha-256 — pgbouncer must speak SCRAM to PG15 (the DB user
# stores a scram verifier, not md5). Without this you'll see:
#   "cannot do SCRAM authentication: wrong password type"
docker exec dating_pgbouncer sh -c 'echo "SHOW POOLS;" | psql -U dating_user -d pgbouncer'
# Expect 1–N pools, e.g. line cl_chat: 20 5 5 ...
```
> PgBouncer uses transaction pooling, 20 default pool / 5 reserve, up to 1000
> clients. Migrations bypass it (direct to `db`) because DDL + transaction pools
> don't mix.

### 5.4 MinIO buckets

```bash
docker compose exec minio-init true    # ran at startup
docker exec dating_minio sh -c 'mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD && mc ls local'
# expect: photos-public/  photos-private/
```

### 5.5 Celery (when CELERY_ENABLED=true)

```bash
docker compose logs celery-worker | tail     # "connected/side ... ready."
docker compose logs celery-beat   | tail     # heartbeats / beat: Starting...
```

### 5.6 GlitchTip (error tracking)

Follow the Quick Start in `docs/server_setup.md:step-7` to create the superuser,
org, project and **DSN**. Backport the DSN value into `.env` `GLITCHTIP_DSN`, then:
`docker compose restart app`.

### 5.7 `/metrics` — internal only

```bash
# from your machine (public) — must be 403:
curl -s -o /dev/null -w '%{http_code}\n' http://1.2.3.4/metrics     # 403

# from inside the stack — must be 200:
docker exec dating_app sh -c 'curl -s http://localhost:8000/metrics | head -1'   # # HELP http_requests_total ...
```
Point your Prometheus scraper at `http://app:8000/metrics` (dns on the internal
network) and Grafana at Prometheus. The endpoint is `include_in_schema=False`
(never shows in Swagger) and nginx returns 403 to the public.

---

## 6. Closing loop: domain + HTTPS

### 6.1 (Optional but recommended) real HTTPS

> **Not yet merged in this release** — nginx ships with a commented `server { listen 443 }`
> block. When you enable HTTPS:

```bash
# Install certbot (official)
apt install -y certbot python3-certbot-nginx
# Obtain a cert (nginx plugin rewrites config for you)
certbot --nginx -d api.yourdomain.com
# Auto-renew
systemctl enable certbot.timer
```

### 6.2 Session/validation

- WS connections use `wss://api.yourdomain.com/api/v1/ws/stream?token=...`.
- Set `CORS_ORIGINS=https://api.yourdomain.com` (or the app origin) when not `*`.

---

## 7. Daily operations

### 7.1 Container status

```bash
docker compose ps
```

### 7.2 Logs

```bash
docker compose logs -f app
docker compose logs -f celery-worker
docker compose logs -f pgbouncer
```

### 7.3 Restart a service

```bash
# App / worker gate-graceful: stop_grace_period=60s, uvicorn
# --timeout-graceful-termination 30 drains WebSockets + tells clients
# server_shutdown before exiting. Clients auto-reconnect.
docker compose restart app
docker compose restart celery-worker celery-beat
```

### 7.4 Seed data (idempotent)

```bash
docker exec dating_app python -m app.db.scripts.seed_interests
docker exec dating_app python -m app.db.scripts.seed_dummy_users   # 1000 dev users local only in prod!
```
> Seeding dummy users in production is almost certainly unwanted — run only on
> a dev/staging box.

### 7.5 Migrations — manual

```bash
docker compose run --rm migrate          # alembic upgrade head (direct to db)
docker compose run --rm migrate alembic current
```

> **Drift guard:** the CI job `migrations` runs `alembic upgrade head && alembic check`
> on a fresh DB; if your models diverge from a migration, CI fails. Keep `app/models/*`
> + `alembic/versions/*` in lockstep or the deploy will be blocked.

---

## 8. Deploy / upgrade cycle

| Who | What |
|-----|------|
| You | commit + `git push origin main` |
| CI | `migrations` job (fresh DB + `alembic check`), `test` job (`pytest tests/done/`) |
| CI ssh step | drift guard (abort if server ≠ origin/main) → fast-forward merge → upload FCM key |
| `scripts/deploy.sh` | build base → `docker compose build app migrate celery-worker celery-beat` → `docker compose run --rm migrate` → `docker compose up -d` (full stack, idempotent) → nginx reload → health check → rollback on failure |

```bash
# Manual deploy (if you aren't using CI)
cd /opt/demo-bondi && bash scripts/deploy.sh
```

### 8.1 What deploy does (as of this release)

- **Server state is never overwritten.** Both the CI ssh step and `deploy.sh`
  refuse to proceed if the server working tree has ANY drift — dirty tracked
  files, untracked non-gitignored files, local commits, or not being on `main`
  (`git status --porcelain` non-empty, `git rev-list origin/main..HEAD` > 0, or
  branch ≠ `main`) → **deploy aborts and the pipeline fails**. Resolve the drift
  first, then push again. Sync is a strict `git merge --ff-only origin/main` —
  no `reset --hard`, no `git clean`.
- `docker compose up -d` is idempotent: containers whose image/config changed are
  recreated; unchanged services (db, redis, minio, glitchtip) stay up. It also
  creates new services/volumes/networks on first boot, so a fresh box needs no
  separate `up -d` step.
- `firebase-service-account.json` and `.env` are gitignored → they never appear
  in `git status --porcelain`, so they neither block deploys nor get touched by
  the merge.
- **Auto-scaling to the box** (dynamic at container start): FastAPI workers =
  `min(2·cores+1, available RAM / 250 MB, 8)` (override with `WEB_WORKERS`);
  celery worker concurrency = `nproc` (override with `CELERY_CONCURRENCY`);
  nginx already uses `worker_processes auto;`.

### 8.2 Rollback

`deploy.sh` snapshots the running image tag before pulling, and if the health
check fails it restores `dating-app:rollback`. If a **recreate** was needed, do a
manual revert after: restore DB (§9), `docker image tag
dating-app:rollback dating-app:latest`, `docker compose up -d --force-recreate`.

---

## 9. Backups & disaster recovery

### 9.1 Postgres

```bash
# one-shot
cd /opt/demo-bondi && ./scripts/backup.sh

# scheduled, every 3 am:
crontab -e
# 0 3 * * * /opt/demo-bondi/scripts/backup.sh >> /var/log/bondi-backup.log 2>&1
```

> Retention: keep 7 days (env in backup.sh). Stored at `/opt/demo-bondi/backups/db_*.sql.gz`.

### 9.2 MinIO data (volume)

```bash
tar czf /opt/demo-minio-volume-$(date +%F).tar.gz -C /var/lib/docker/volumes/ demo-bondi_minio_data
```

### 9.3 Restore

```bash
./scripts/restore.sh backups/db_2026-08-01_031500.sql.gz   # usage: <file>
```
It stops `app`, drops+recreates `dating_db`, re-plays the dump, starts `app`.

---

## 10. Firewall

> Share `scripts/firewall.sh` — ports 22/80/443 only. `8080` (GlitchTip) is
> **not** opened by default; open it **only** if you want remote dashboards.

```bash
bash scripts/firewall.sh
ufw status verbose
```

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| App shows `redis` connection refused at boot | `REDIS_PASSWORD` mismatch between `redis` and `.env` | Align `REDIS_PASSWORD`; `docker compose up -d redis app` |
| `too many clients already` | 20-pool saturated; hitting hard limit | watch `SHOW POOLS` + `pg_stat_activity`; pg `max_connections=200` in compose, pgbouncer pool sized for it |
| Photos 404 / “minio” DNS error on client | `S3_ENDPOINT_URL`/`S3_PUBLIC_BASE_URL` still `http://minio` or `:9000` | set to **public host**, e.g. `http://api.yourdomain.com` |
| `/api/docs` 404 | prod disables openapi | disable `ENVIRONMENT=development` only in dev |
| `502 Bad Gateway` | app crashed/starting | `docker compose logs app` |
| Celery tasks never run | `CELERY_ENABLED=true` but no worker | `docker compose up -d celery-worker celery-beat`; logs |
| `/metrics` public 200 | nginx `location = /metrics` missing | ensure `nginx/nginx.conf` has deny-block (commit default) |
| Migration drift fails in CI | model changed without a revision | add a migration or `alembic revision --autogenerate` and commit |

---

## 12. CI/CD reference

`.github/workflows/deploy.yml` — two jobs:
- `migrations`: fresh DB → `alembic upgrade head` + `alembic check` (drift gate)
- `test`: full `pytest tests/done/`
- `deploy` (needs both): ssh-action → `scripts/deploy.sh`

Required GitHub secrets:
`VPS_HOST`, `VPS_USERNAME`, `VPS_PASSWORD`, `DOCKERHUB_USERNAME`,
`DOCKERHUB_TOKEN`, `FCM_SERVICE_ACCOUNT_JSON`.

---

## 13. Upgrade-path checklist (after this docs-era)

- [ ] Redis: `REDIS_PASSWORD` set (never empty in prod)
- [ ] MinIO: strong root creds rotation (`docker compose up -d minio minio-init`)
- [ ] Enable HTTPS (P0-5 once merged), switch all URLs to: `https://api.yourdomain.com`
- [ ] Real ZarinPal merchant ID (P0-6) + switch `ZARINPAL_SANDBOX=false`
- [ ] Real SMTP (P0-7) before sign-ups expect emails
- [ ] NSFW model integration (P2-7) — heavy infer on the celery worker
- [ ] Backup cron + test a restore at least once a month
- [ ] Grafana dashboard wired to `/metrics`

---

_Generated 2026-08-09. Companion: `docs/server_setup.md` (older, less current),
docs/deploy.md (secrets + pgbouncer notes), scripts/deploy.sh, scripts/backup.sh._