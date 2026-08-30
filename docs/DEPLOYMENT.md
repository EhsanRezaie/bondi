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
             │  bondi_glitchtip (live on :8080)        │
             └───────────────────────────────────┘
```

### 1.2 Networks

| Network | Name | Visibility | Attached to |
|---------|------|-----------|-------------|
| `frontend` | `bondi_frontend` | Public bridge | `nginx`, `app`, `celery-worker`, `celery-beat` (outbound egress for FCM) |
| `internal` | `bondi_internal` | **internal (no internet)** | db, pgbouncer, redis, minio, minio-init, bondi_glitchtip*, migrate, plus nginx/app/workers (media proxy + DB/Redis) |

- The `internal` network is created with `internal: true` — containers on it can
  only reach each other; nothing there is reachable from the host or internet.
- `nginx` sits on **both** networks: public side (`frontend`) serves HTTP, while
  attaching to `internal` is what lets it proxy `/photos-*` to `minio:9000`.
- `celery-worker`/`celery-beat` also join both: `internal` for DB (via pgbouncer)
  + Redis, `frontend` so FCM push calls can reach Google.
- Only `nginx` (80/443) and `bondi_glitchtip` (8080) publish host ports.
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
git clone git@github.com:EhsanRezaie/bondi.git demo-bondi && cd demo-bondi
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
POSTGRES_USER=bondi_admin
POSTGRES_PASSWORD=<openssl rand -hex 16>            # 📌
POSTGRES_DB=bondi

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
GLITCHTIP_DSN=http://public-key@bondi_glitchtip:80/2      # from step 5.6

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
> `celery-worker`, `celery-beat`) and the two networks (`bondi_frontend`,
> `bondi_internal`) on first boot. A one-time manual hand-rolled first boot is
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
docker exec bondi_redis redis-cli -a "$REDIS_PASSWORD" ping     # PONG
# without password it must now fail (auth-protected):
docker exec bondi_redis redis-cli ping                            # (error)
```

### 5.3 PgBouncer

```bash
# AUTH_TYPE is scram-sha-256 — pgbouncer must speak SCRAM to PG15 (the DB user
# stores a scram verifier, not md5). Without this you'll see:
#   "cannot do SCRAM authentication: wrong password type"
docker exec bondi_pgbouncer sh -c 'echo "SHOW POOLS;" | psql -U bondi_admin -d pgbouncer'
# Expect 1–N pools, e.g. line cl_chat: 20 5 5 ...
```
> PgBouncer uses transaction pooling, 20 default pool / 5 reserve, up to 1000
> clients. Migrations bypass it (direct to `db`) because DDL + transaction pools
> don't mix.

### 5.4 MinIO buckets

```bash
docker compose exec minio-init true    # ran at startup
docker exec bondi_minio sh -c 'mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD && mc ls local'
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
docker exec bondi_app sh -c 'curl -s http://localhost:8000/metrics | head -1'   # # HELP http_requests_total ...
```
Point your Prometheus scraper at `http://app:8000/metrics` (dns on the internal
network) and Grafana at Prometheus. The endpoint is `include_in_schema=False`
(never shows in Swagger) and nginx returns 403 to the public.

---

## 6. Closing loop: domain + HTTPS/SSL

This section is the complete, step-by-step recipe to put the whole stack behind a
single TLS entry point on a real domain. It was validated on a live server for
`bondiapp.ir` (API) + `www.bondiapp.ir` (redirect) + `admin.bondiapp.ir`
(admin panel) with one Let's Encrypt certificate covering all three names.
Substitute your own domain everywhere; nothing here is host-specific.

> **Architecture:** the **backend nginx** container (`bondi_nginx`) is the only
> TLS terminator. It owns host ports `80`/`443`, and routes by `server_name`:
>
> | Hostname | Routes to |
> |----------|-----------|
> | `bondiapp.ir`, `www.bondiapp.ir` | FastAPI `app` (API + `/api/v1/ws/`) and `minio` (`/photos-*`) |
> | `admin.bondiapp.ir` | SPA via `http://bondi_admin:80` for `/` and straight to `app` for `/api/v1/` |
> | `invite.bondiapp.ir` | static invite site via `http://invite-site:80` (own cert, §6.11) |
>
> The admin container keeps its own plain-HTTP nginx and is joined to the
> `bondi_frontend` docker network so `bondi_nginx` can reach it. Admin API calls
> (`/api/v1/…`) are forwarded by `bondi_nginx` **directly to `app`** to avoid an
> nginx→nginx proxy loop. One certificate, three SANs, one renewal job.

### 6.0 DNS records

Create **A records** at your DNS provider (TTL 300 for fast iteration):

```text
bondiapp.ir.          A  1.2.3.4
www.bondiapp.ir.      A  1.2.3.4
admin.bondiapp.ir.    A  1.2.3.4
```

Verify propagation before issuing the certificate (certbot HTTP-01 needs port
80 to reach the challenge for **every** name in the cert):

```bash
dig +short bondiapp.ir admin.bondiapp.ir www.bondiapp.ir
```

### 6.1 Backend config changes (in the repo)

All of this is committed to `origin/main` (so the server can `git pull --ff-only`
without tripping the drift guard — see §8.1).

**`nginx/nginx.conf`** — three changes:

1. In the port-80 `server`, add a webroot block **before** the catch-all so
   certbot's HTTP-01 challenge is served (this keeps API working on port 80 too,
   so already-installed builds don't break mid-migration):

   ```nginx
   location /.well-known/acme-challenge/ {
       root /var/www/certbot;
   }
   ```

2. Add a `listen 443 ssl;` server block for the API names:

   ```nginx
   server {
       listen 443 ssl;
       http2 on;
       server_name bondiapp.ir www.bondiapp.ir;

       ssl_certificate     /etc/letsencrypt/live/bondiapp.ir/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/bondiapp.ir/privkey.pem;
       ssl_protocols TLSv1.2 TLSv1.3;
       ssl_ciphers HIGH:!aNULL:!MD5;

       add_header Strict-Transport-Security "max-age=63072000" always;
       add_header X-Frame-Options "SAMEORIGIN" always;
       add_header X-Content-Type-Options "nosniff" always;
       add_header X-XSS-Protection "1; mode=block" always;

       location ~ ^/api/v1/auth/ { limit_req zone=auth burst=10 nodelay; proxy_pass http://app; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
       location ~ ^/api/v1/ws/  { proxy_pass http://app; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_read_timeout 86400; }
       location /photos-public { proxy_pass http://minio:9000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; }
       location /photos-private{ proxy_pass http://minio:9000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; }
       location = /metrics { deny all; return 403; }
       location /            { limit_req zone=api burst=50 nodelay; proxy_pass http://app; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
   }
   ```

3. Add a `listen 443 ssl;` server block for the admin panel (API straight to
   `app`, SPA to the admin container):

   ```nginx
   server {
       listen 443 ssl;
       http2 on;
       server_name admin.bondiapp.ir;

       ssl_certificate     /etc/letsencrypt/live/bondiapp.ir/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/bondiapp.ir/privkey.pem;
       ssl_protocols TLSv1.2 TLSv1.3;

       add_header Strict-Transport-Security "max-age=63072000" always;
       add_header X-Frame-Options "SAMEORIGIN" always;
       add_header X-Content-Type-Options "nosniff" always;

       location /api/v1/ {
           proxy_pass http://app;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
       location / {
           proxy_pass http://bondi_admin:80;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       }
   }
   ```

**`docker-compose.yml`** — the `nginx` service gets two bind mounts so it can
serve the ACME challenge and read renewed certs (no copy step on renewal):

```yaml
    volumes:
      - nginx_certs:/etc/nginx/certs
      - /var/www/certbot:/var/www/certbot:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
```

> `nginx_certs` (the old volume) can be removed once nothing references it; it is
> harmless to keep.

### 6.2 Roll the new config out to the server

> ⚠️ **First-time ordering (chicken-and-egg).** The committed `nginx.conf`
> includes the `listen 443 ssl` blocks, which reference
> `/etc/letsencrypt/live/bondiapp.ir/…` — files that don't exist until step 6.3.
> nginx **will not start** with a missing certificate file, so on the very first
> setup you must:
>
> 1. Deploy a **bootstrap** config — the committed file with the two `443` server
>    blocks stripped (keeps the ACME webroot on `:80` so the challenge can be
>    served), start nginx, and issue the cert (§6.3).
> 2. Then restore the full committed config and force-recreate nginx.
>
> `git checkout -- nginx/nginx.conf` re-keys the tree to `origin/main`, so the
> drift guard in §8.1 stays happy.

```bash
# on the server, in the backend clone (e.g. /opt/demo-bondi):
git pull --ff-only
docker compose config -q                          # validates interpolation

# ---- FIRST-TIME ONLY: bootstrap without the 443 blocks ----
python3 - <<'PY'
content = open('nginx/nginx.conf').read()
marker = '# ----- HTTPS (TLS termination'
prefix = content.split(marker)[0].rstrip() + '\n}\n'
open('nginx/nginx.conf', 'w').write(prefix)
PY
docker compose up -d --force-recreate nginx
# ... run §6.3 (certbot) now ...
git checkout -- nginx/nginx.conf                 # restore full config
# ------------------------------------------------------------

# Recreate with --force-recreate: nginx.conf is mounted as a compose config and
# some compose versions don't rebuild it on a plain `up -d` (verified on Docker
# 29.x / Compose v2 — the container kept the old config until forced).
docker compose up -d --force-recreate nginx
docker compose exec nginx nginx -t                # test the active config
docker exec bondi_nginx nginx -T | grep -E 'listen|ssl_certificate '   # confirm 443 present
```

### 6.3 Issue the certificate (certbot webroot)

```bash
apt install -y certbot
mkdir -p /var/www/certbot
# On first setup this runs during §6.2's bootstrap step (nginx is already
# serving the ACME challenge on port 80 from the stripped config):
certbot certonly --webroot -w /var/www/certbot \
  -d bondiapp.ir -d www.bondiapp.ir -d admin.bondiapp.ir \
  -m you@example.com --agree-tos --no-eff-email
```

> HTTP-01 needs port 80 open to the internet and all three names resolving. If
> certbot fails on `admin.bondiapp.ir`, the DNS record isn't propagated yet.

### 6.4 Auto-renewal (reload the *containerized* nginx)

Debian/Ubuntu ship `certbot.timer`; enable it, then add a **deploy hook** so a
renewed cert is picked up by the container (certs are bind-mounted, so a reload
is enough — no restart, no copy):

```bash
systemctl enable --now certbot.timer
cat > /etc/letsencrypt/renewal-hooks/deploy/nginx-reload.sh <<'EOF'
#!/bin/sh
docker compose -f /opt/demo-bondi/docker-compose.yml exec nginx nginx -s reload
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/nginx-reload.sh
```

> Adjust `/opt/demo-bondi` to your actual clone path. Verify:
> `certbot renew --dry-run`.

### 6.5 Backend `.env` — switch everything to HTTPS

The app builds **public** media URLs from these values, so they must be the
public https origin (not `minio`/`:9000` — clients can never reach those):

```env
# Used by boto3 for S3 ops AND for signing presigned URLs. Pointing it at the
# public https host makes signed URLs (pending photos, chat media) loadable by
# clients; nginx preserves the Host header so SigV4 still validates in MinIO.
S3_ENDPOINT_URL=https://bondiapp.ir
S3_PUBLIC_BASE_URL=https://bondiapp.ir/photos-public

# Browser origins allowed to call the API (admin panel is a different origin):
CORS_ORIGINS=https://admin.bondiapp.ir

# Payments callback must be public + https:
ZARINPAL_CALLBACK_URL=https://bondiapp.ir/api/v1/subscriptions/zarinpal/callback
```

Restart the app so the new URLs take effect:

```bash
docker compose up -d app
```

> ⚠️ After changing `S3_ENDPOINT_URL`, verify private/chat media — cached
> presigned URLs in Redis were signed against the old host and will 403 until
> their 5-min cache expires. Nothing to do; just smoke-test after a few minutes.

### 6.6 Admin panel changes

**`bondi_admin/docker-compose.yml`** — join the shared frontend network (so
`bondi_nginx` can reach the admin container by name) and stop publishing `8443`
to the world (localhost only keeps the CI healthcheck working):

```yaml
services:
  admin:
    # ...
    ports:
      - "127.0.0.1:8443:80"
    networks:
      - default
      - bondi_frontend

networks:
  bondi_frontend:
    external: true
```

`nginx/nginx.conf.template`, `BACKEND_ORIGIN` and the CI workflow need **no
change** — the domain path is served through `bondi_nginx`, which proxies
`/api/v1/` directly to the backend, and the admin nginx never sees those
requests. (`BACKEND_ORIGIN` only matters for direct `127.0.0.1:8443` access.)

Deploy: `docker compose up -d` in `/opt/bondi-admin` (or push to `main` and let
the CI SCP step overwrite the files).

### 6.7 Mobile app changes

**`.env`** — point the app at the https origin (WS must be `wss`):

```env
API_BASE_URL=https://bondiapp.ir/api/v1
WS_BASE_URL=wss://bondiapp.ir/api/v1
```

**`android/app/src/main/AndroidManifest.xml`** — once *all* traffic is https,
drop the cleartext exception (recommended hardening):

```xml
<application
    android:label="Bondi"
    android:name="${applicationName}"
    android:icon="@mipmap/ic_launcher">
```

Rebuild the APK. Native Google Sign-In needs no change (it does not use the
domain); if you ever build a web client, add `https://bondiapp.ir` to the
Firebase console's authorized domains.

### 6.8 Firewall

```bash
bash scripts/firewall.sh     # opens 22/80/443 only
ufw status verbose
```

The admin panel is now only reachable through `https://admin.bondiapp.ir`;
`8443` (localhost-only), `8080` (GlitchTip) and `9001` (MinIO console) are not
exposed externally. Open them only if you truly need remote dashboards.

### 6.9 Verification checklist

```bash
# API over TLS + valid cert chain
curl -I https://bondiapp.ir/api/v1/auth/health
#   expect: HTTP/1.1 200 OK ... Strict-Transport-Security ... 

# www → apex on the same cert
curl -sI https://www.bondiapp.ir/api/v1/auth/health | head -1

# public media still resolves over https
curl -sI https://bondiapp.ir/photos-public/<some-object-key>

# admin SPA + its API calls
curl -sI https://admin.bondiapp.ir/                          # 200, HTML
curl -s -o /dev/null -w '%{http_code}\n' https://admin.bondiapp.ir/api/v1/auth/health

# WebSocket (token-optional health path)
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: x" -H "Sec-WebSocket-Version: 13" \
  https://bondiapp.ir/api/v1/ws/... 

# Cert + renewal state
certbot certificates
systemctl is-active certbot.timer
certbot renew --dry-run
```

In the app: login, upload a photo, open chat media + voice, verify the app is
now hitting `https://bondiapp.ir`.

### 6.10 Troubleshooting / rollback

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `certbot` can't reach challenge | DNS not propagated / port 80 blocked | `dig +short` each name; `ufw allow 80`; confirm §6.2 applied |
| nginx won't start after config change | typo in a server block | `docker compose exec nginx nginx -t`; fix and `docker compose up -d nginx` |
| Media 403 / "minio" host in URLs | `S3_ENDPOINT_URL`/`S3_PUBLIC_BASE_URL` stale | set to public https host (§6.5), `docker compose up -d app` |
| Presigned URLs 403 right after switch | Redis cached old-host signatures | wait ≤5 min (cache TTL) |
| Admin loads but API 502 | `bondi_nginx` can't reach admin/api | check `bondi_admin` is on `bondi_frontend` network; `docker compose logs nginx` |
| Old app builds break after forcing 443-only | clients still on `http://<ip>` | keep port 80 serving API during transition, or rebuild the app |
| Renewed cert not used | deploy hook missing / wrong path | run hook manually; fix path in `/etc/letsencrypt/renewal-hooks/deploy/nginx-reload.sh` |

**Rollback:** revert the repo commits (`git revert`/`git push`), `git pull` on the
server, `docker compose up -d nginx` (port-80-only config), and restore the
previous `.env` values (`S3_ENDPOINT_URL`, `S3_PUBLIC_BASE_URL`) + `docker compose up -d app`.

### 6.11 Adding another subdomain/service (e.g. `invite.bondiapp.ir`)

Extra static sites (side projects, landing pages) ride the same TLS front with
**their own separate cert** and a `server` block. Verified live for
`invite.bondiapp.ir` (a tiny nginx container serving a static page):

1. **DNS:** add `A` record `invite.bondiapp.ir → <server ip>`; wait for propagation.
2. **Serve the site** (if it isn't already): the `invite-site` container runs on
   the shared frontend network so `bondi_nginx` can reach it by name, and its raw
   port is bound to localhost only (not public):

   ```bash
   cd /root/invite-app
   docker build -t invite-site .
   docker run -d --name invite-site --restart unless-stopped \
     --network bondi_frontend -p 127.0.0.1:8143:80 invite-site
   # from then on it is only reachable as https://invite.bondiapp.ir (and via
   # http://127.0.0.1:8143 on the host itself)
   ```
3. **Issue its own cert** (the port-80 catch-all already serves the webroot for
   any hostname, so no config change is needed to obtain it):
   ```bash
   certbot certonly --webroot -w /var/www/certbot -d invite.bondiapp.ir \
     --agree-tos --no-eff-email --register-unsafely-without-email
   ```
4. **`nginx/nginx.conf`** (committed): add a port-80 redirect block (with the ACME
   webroot location so renewals keep working) and a `443` block referencing the
   new cert, proxying `/` to `http://invite-site:80`:

   ```nginx
   server {
       listen 80;
       server_name invite.bondiapp.ir;
       location /.well-known/acme-challenge/ { root /var/www/certbot; }
       location / { return 301 https://invite.bondiapp.ir$request_uri; }
   }
   server {
       listen 443 ssl;
       http2 on;
       server_name invite.bondiapp.ir;
       ssl_certificate     /etc/letsencrypt/live/invite.bondiapp.ir/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/invite.bondiapp.ir/privkey.pem;
       ssl_protocols TLSv1.2 TLSv1.3;
       location / {
           proxy_pass http://invite-site:80;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
   }
   ```
5. **Deploy:** commit + push, then on the server `git fetch origin &&
   git merge --ff-only origin/main` (fetch matters — a stale local `origin/main`
   ref deploys nothing) and `docker compose up -d --force-recreate nginx`
   (compose-config change; `--force-recreate` is required on this compose version).

Renewal needs **no new hook**: the single deploy hook `nginx-reload.sh` reloads
`bondi_nginx` for any renewed cert, so both the `bondiapp.ir` and
`invite.bondiapp.ir` certs are picked up automatically.

> ⚠️ A container recreated outside this run command loses its
> `bondi_frontend` attachment — re-run it (or `docker network connect
> bondi_frontend invite-site`) after any manual `docker run`.

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
docker exec bondi_app python -m app.db.scripts.seed_interests
docker exec bondi_app python -m app.db.scripts.seed_dummy_users   # 1000 dev users local only in prod!
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
| CI | `migrations` job (fresh DB + `alembic check`), `test` job (`pytest tests/`) |
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
  recreated; unchanged services (db, redis, minio, bondi_glitchtip) stay up. It also
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
It stops `app`, drops+recreates `bondi`, re-plays the dump, starts `app`.

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
- `test`: full `pytest tests/`
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