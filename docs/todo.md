# Bondi — Master TODO (detailed)

> **Single source of truth for all remaining work.**
> Merged from: `docs/dev.md`, `docs/performance_plan.md`, `docs/scale_plan.md`,
> `docs/security_plan.md`, `docs/server_setup.md` + a senior-dev/system-designer audit
> of the entire backend codebase.
>
> The old docs have **stale checklists** — many unchecked boxes are already done
> (WebSocket Pub/Sub, presence, typing, health checks, structlog, FCM, swipe dedup,
> discover cache). This file reconciles all of that and adds the new issues the audit
> found (several critical, none in any doc).
>
> Each task lists: **Evidence** (file:line) → **Why** → **Fix** (step-by-step with code)
> → **Files to touch** → **Commands** → **Verify** → **Gotchas**.
> Mark `[ ]` → `[x]` as you ship.

---

## Legend

| Tag | Meaning |
|-----|---------|
| 🔴 P0 | Launch blocker / will break or endanger production |
| 🟠 P1 | Must before scaling (100+ concurrent users) |
| 🟡 P2 | Backend hardening / correctness |
| 🟢 P3 | Security hardening |
| 🔵 P4 | Observability & operations |
| 🟣 P5 | Flutter app (from performance_plan Phase 5) |
| ⚫ P6 | Deployment checklist (from dev.md) |
| ✅ | Already done (listed only to kill the stale checklist) |

Effort: `XS` <1h · `S` ~1 session · `M` ~1–2 sessions · `L` multi-session.

---

## Status snapshot (reconciled with code, 2026-08-02)

| Area | Status | Note |
|------|--------|------|
| DB indexes, Redis caching, Haversine, N+1 fixes, cursor pagination | ✅ | confirmed in models/endpoints |
| BackgroundTasks for notifications | ✅ | but no durable queue → P1-7 |
| GZip, structlog, GlitchTip/Sentry | ✅ | `main.py`, `core/logging.py` |
| FCM push + device-token endpoint | ✅ | but blocking → P1-1 |
| Auth hardening (15-min token, OTP, enumeration, timing) | ✅ | `security.py`, `redis.py` |
| Location fuzzing ±500m | ✅ | `utils/geo.py` |
| Swipe dedup + discover stack cache | ✅ | `cache.py` |
| WebSocket multi-worker (Redis Pub/Sub), presence, typing | ✅ | `websocket_manager.py` |
| Docker health checks + dynamic multi-worker | ✅ | `entrypoint.sh` |
| Nginx reverse proxy (HTTP only) | ✅ | HTTPS commented → P0-5 |
| HTTPS / TLS | ❌ | → P0-5 |
| Real ZarinPal | ❌ mocked | → P0-6 |
| Email sending | ❌ stub | → P0-7 |
| PgBouncer / pool tuning | ❌ | → P0-4 |
| NSFW moderation | ⚠️ skin heuristic | → P2-7 |
| **Migrations in version control** | ✅ committed | P0-1 done |
| **Startup auto-generates migrations** | ✅ removed | P0-1 done |
| **Auth dependency hits DB every request** | ✅ cached in Redis | P0-2 done |
| **Chat decryption CPU per-message** | ✅ cached + offloaded | P0-3 done |
| **Celery installed but 0 bytes of code** | ❌ dead | → P1-7 (NEW) |

## 🔴 P0 — Critical (launch blockers / will break production)

### P0-1 — Stop auto-generating migrations on startup AND commit migrations to git · `XS` · NEW

- [x] Done (commit `b3d76b2`)

**Evidence:** `entrypoint.sh:4-5` runs `alembic revision --autogenerate -m "auto: startup migration"` on **every** container start; `.gitignore` contains `alembic/versions/*.py`; `git ls-files alembic/versions/` returns **empty** → migrations are NOT in version control.

**Why critical:** On every deploy/restart a new no-op migration file is generated locally and never committed. Fresh clone / CI / new server has **zero migrations** — the schema is whatever `--autogenerate` produces at boot, unreviewed. With blue/green or scale-out, two containers race to write/apply migrations. `|| true` swallows all errors. No downgrade path, no audit history. This is the biggest reproducibility/safety hole in the project.

**Fix (step-by-step):**
1. Edit `entrypoint.sh` — delete the autogenerate line, keep only the upgrade:
   ```sh
   # DELETE THIS LINE:
   # alembic revision --autogenerate -m "auto: startup migration" 2>&1 || true
   # KEEP:
   echo "Applying migrations..."
   alembic upgrade head
   ```
2. Remove the gitignore rule so migrations are tracked:
   ```diff
   # .gitignore
   -# Alembic
   -alembic/versions/*.py
   ```
   Then: `git add alembic/versions/*.py && git commit -m "chore: track alembic migrations"`
3. Split migration from app boot — add a one-shot `migrate` service in `docker-compose.yml`:
   ```yaml
   migrate:
     build: .
     container_name: dating_migrate
     env_file: .env
     environment:
       DATABASE_URL: postgresql+asyncpg://dating_user:dating_pass@db:5432/dating_db
     command: alembic upgrade head
     depends_on:
       db: { condition: service_healthy }
     restart: "no"        # run once, don't loop
   app:
     depends_on:
       migrate: { condition: service_completed_successfully }  # wait for it
   ```
4. Make the `app` entrypoint NOT run migrations (it only seeds). Keep seeding in `entrypoint.sh`.

**Files to touch:** `entrypoint.sh`, `.gitignore`, `docker-compose.yml`, `docker-compose.test.yml` (optional).
**Commands:** `git add alembic/versions/ && alembic upgrade head && pytest tests/done/ -q`.
**Verify:** `docker compose up -d` → `docker compose logs migrate` shows the upgrade ran once; `docker exec dating_db psql -U dating_user -d dating_db -c "select * from alembic_version"` shows one head.
**Gotchas:** The first committed migration must reflect the **current** prod schema exactly. Generate it cleanly once: `alembic revision --autogenerate -m "baseline"`, eyeball the diff against the live DB (`alembic check`), then commit. Never commit an empty/`pass` migration as the baseline.

### P0-2 — Cache `get_current_user` in Redis (DB hit on every authenticated request) · `S` · NEW

- [x] Done

**Evidence:** `app/core/deps.py:54-84` runs `select(User).options(selectinload(profile), selectinload(settings)).where(User.id == user_id)` on **every** authed endpoint. That's 1 query + 2 selectinload queries = **3 DB round-trips per request just to authenticate**. `/users/me` response is cached (`users.py:38,62`) but that doesn't help the other ~25 endpoints.

**Why critical:** At 100 RPS that's 300 DB round-trips/sec for auth alone — before any real work. It's the single biggest latency + DB-load win available, and it's invisible (every endpoint pays it).

**Fix (step-by-step):**
1. Add a serializer + cache helpers in `app/core/cache.py`:
   ```python
   TTL_AUTH_USER = 30  # seconds

   def key_auth_user(user_id: UUID, token_version: int) -> str:
       return f"cache:auth:{user_id}:v{token_version}"

   async def get_cached_auth_user(redis, user_id, token_version):
       try:
           raw = await redis.get(key_auth_user(user_id, token_version))
           return json.loads(raw) if raw else None
       except Exception:
           return None

   async def invalidate_auth_user(redis, user_id):
       # token_version changed → old key won't match anyway, but clear broadly:
       try:
           async for k in redis.scan_iter(match=f"cache:auth:{user_id}:v*"):
               await redis.delete(k)
       except Exception:
           pass
   ```
2. Rewrite `get_current_user` in `deps.py` to try Redis first, fall back to DB:
   ```python
   async def get_current_user(credentials=Depends(security), session=Depends(get_session)):
       # ... decode token, get user_id + token_version (existing) ...
       cached = await get_cached_auth_user(redis_client, user_id, token_version)
       if cached:
           return _user_from_cache(cached)   # reconstruct User-like object
       # DB load (existing), then cache it:
       user = result.scalar_one_or_none()
       await cache_set(redis_client, key_auth_user(user_id, user.token_version),
                      _user_to_cache(user), TTL_AUTH_USER)
       return user
   ```
3. Invalidate everywhere `token_version` changes or profile/settings mutate:
   - `auth.py` change-password / password-reset (`token_version += 1`) → `invalidate_auth_user` + `invalidate_user_cache`
   - `users.py` PUT /me, interests, prompts, location → `invalidate_user_cache` (already) + `invalidate_auth_user`
   - admin ban/deactivate → `invalidate_auth_user`
4. Because the cache key includes `token_version`, a password change auto-invalidates (old version's key is never requested) — safe.

**Files to touch:** `app/core/cache.py`, `app/core/deps.py`, `app/api/v1/endpoints/auth.py`, `app/api/v1/endpoints/users.py`, admin endpoints.
**Commands:** `pytest tests/done/test_auth.py tests/done/test_users.py -v`.
**Verify:** Add a counter; call the same endpoint twice with the same token → DB query count should drop from 3 to 0 on the 2nd call. `EXISTS cache:auth:<id>:v1` in Redis.
**Gotchas:**
- Don't cache the full SQLAlchemy ORM instance (it's bound to a session). Cache a plain dict and reconstruct a lightweight object, OR cache the already-serialized `/users/me` response and have `get_current_user` return a minimal dataclass. The cleanest: cache id+token_version+is_active+profile_dict+settings_dict.
- `token_version` is the cache-buster — make sure *every* password change/ban increments it (it already does).
- Keep TTL short (30s) so a ban applied out-of-band propagates within 30s even without explicit invalidation.

### P0-3 — Fix chat-history decryption CPU cost (PBKDF2 100k × N rows on event loop) · `S` · NEW

- [x] Done

**Evidence:** `app/core/encryption.py:27-36` derives the per-match key with PBKDF2-HMAC-SHA256, **100,000 iterations**. `app/models/message.py:63-76` re-derives + decrypts in the `content` property **on every access**. `messages.py` chat-history loader iterates rows and accesses `.content` per row.

**Why critical:** Loading 30 messages = 30 × (PBKDF2-100k + AES-GCM decrypt). PBKDF2-100k is intentionally slow (~50–100 ms on CPU). That's **1.5–3 seconds of pure CPU per chat page, on the event-loop thread**. A few users loading chat simultaneously stall the entire server and tank p99 for everyone.

**Fix (step-by-step):**
1. Derive the key **once per match** and cache it in-process with an LRU (keys are derived from a server secret + match_id — safe to cache):
   ```python
   # app/core/encryption.py
   from functools import lru_cache

   @lru_cache(maxsize=4096)
   def derive_chat_key(match_id: str) -> bytes:
       salt = match_id.encode("utf-8")
       kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
       return kdf.derive(settings.ENCRYPTION_SECRET.encode("utf-8"))
   ```
   Now 30 messages → 1 derivation + 30 cheap AES-GCM decrypts.
2. Move decryption off the event loop. Add an async helper:
   ```python
   import asyncio
   async def decrypt_many(rows: list[tuple[str, str]], match_id: str) -> list[str]:
       key = derive_chat_key(match_id)
       loop = asyncio.get_running_loop()
       def _work():
           out = []
           for enc, mid in rows:
               try:
                   out.append(_decrypt_with_key(enc, key))
               except Exception:
                   out.append(enc)  # fallback
           return out
       return await loop.run_in_executor(None, _work)
   ```
3. In `messages.py` `get_chat_history`, collect `(encrypted_content, match_id)` rows and call `decrypt_many(...)` once instead of touching `.content` per row.
4. (Optional, bigger win) The per-match key is derived from a **server secret**, not a user password — PBKDF2-100k is overkill. Switch to a single HMAC-SHA256 derivation (µs) once you've confirmed no compliance requirement forces PBKDF2. This makes decryption ~100× cheaper.

**Files to touch:** `app/core/encryption.py`, `app/models/message.py` (keep property for single-message use, but bulk paths shouldn't use it), `app/api/v1/endpoints/messages.py`, `app/services/chat_service.py`.
**Commands:** `pytest tests/done/test_messages*.py -v`.
**Verify:** Benchmark `GET /messages/{id}?limit=30` — should drop from ~2s to <50ms. Watch CPU during concurrent chat loads.
**Gotchas:**
- The `content` property is used by admin endpoints and single-message reads — keep it working; just don't use it in bulk loops.
- If you switch the KDF, you must re-encrypt all existing messages (a one-time migration). Add `scripts/reencrypt_messages.py` that decrypts with old key, encrypts with new. See P0-8 rotation script — same pattern.

### P0-4 — Add PgBouncer + tune SQLAlchemy pool · `S`

- [ ] Done

**Evidence:** `app/db/session.py:8-12` creates the engine with **no** `pool_size`/`max_overflow` (defaults 5/10). No pooler in `docker-compose.yml`. `entrypoint.sh:21-27` runs up to 8 workers → 8 × 15 = **120 possible connections** vs Postgres default `max_connections=100`.

**Why:** First symptom at ~100 concurrent users: `asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already`.

**Fix (step-by-step):**
1. Add a `pgbouncer` service in `docker-compose.yml`:
   ```yaml
   pgbouncer:
     image: edoburu/pgbouncer:latest
     container_name: dating_pgbouncer
     restart: unless-stopped
     environment:
       DB_HOST: db
       DB_USER: dating_user
       DB_PASSWORD: dating_pass
       DB_NAME: dating_db
       POOL_MODE: transaction
       MAX_CLIENT_CONN: 1000
       DEFAULT_POOL_SIZE: 20
       MIN_POOL_SIZE: 5
       RESERVE_POOL_SIZE: 5
       SERVER_RESET_QUERY: DISCARD ALL
     depends_on:
       db: { condition: service_healthy }
     # expose 6432 internally; do NOT publish to host in prod
   ```
2. Point the app at PgBouncer. In `docker-compose.yml` app env override:
   ```yaml
   environment:
     DATABASE_URL: postgresql+asyncpg://dating_user:dating_pass@pgbouncer:5432/dating_db
   ```
3. Tune the SQLAlchemy engine in `app/db/session.py` — small per-worker pool (PgBouncer does the real pooling) and disable the asyncpg prepared-statement cache (incompatible with transaction-mode PgBouncer):
   ```python
   engine = create_async_engine(
       settings.DATABASE_URL,
       echo=False,
       pool_pre_ping=True,
       pool_size=5,
       max_overflow=0,            # hard cap; PgBouncer is the real pool
       pool_recycle=1800,        # recycle connections every 30 min
       connect_args={
           "statement_cache_size=0",
           "prepared_statement_cache_size=0",
       },
   )
   ```
4. Raise Postgres `max_connections` as a safety net: `command: postgres -c max_connections=200`.

**Files to touch:** `docker-compose.yml`, `app/db/session.py`, `.env` (DATABASE_URL if running locally without compose).
**Commands:** `docker compose up -d pgbouncer app && docker compose logs pgbouncer`.
**Verify:** `docker exec dating_db psql -U dating_user -c "select count(*) from pg_stat_activity"` stays ~20 under load. Load-test with `wrk`/`locust` at 100 concurrent — no `TooManyConnections`.
**Gotchas:**
- PgBouncer transaction mode breaks server-side cursors and `LISTEN/NOTIFY`. The WebSocket manager uses Redis Pub/Sub (not PG NOTIFY), so you're fine — but don't add PG NOTIFY later without switching to session mode.
- GlitchTip uses the same Postgres; give it its own small pool (P2-14).

### P0-5 — Enable HTTPS / TLS · `S`

- [ ] Done

**Evidence:** `nginx/nginx.conf:39-79` only `listen 80`; the entire 443 server block (`:81-125`) is commented out. `.env:119` `GLITCHTIP_DSN=...@localhost:8080/1` (unreachable from the app container in prod). Play Store rejects apps that transmit credentials over HTTP.

**Fix (step-by-step):**
1. Point `api.<your-domain>` DNS A-record at the VPS.
2. On the server: `sudo apt install certbot && sudo certbot certonly --standalone -d api.<your-domain>`.
3. Uncomment the 443 server block in `nginx/nginx.conf`; fix cert paths to `/etc/letsencrypt/live/api.<your-domain>/fullchain.pem` and `privkey.pem`. Add:
   ```nginx
   add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
   ```
4. Mount the certs into the nginx container in `docker-compose.yml`:
   ```yaml
   nginx:
     volumes:
       - /etc/letsencrypt:/etc/letsencrypt:ro
       - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
   ```
5. Set `GLITCHTIP_DSN` in `.env` to the **public** GlitchTip URL (or `http://<publickey>@glitchtip:80/1` for in-Docker).
6. Force HTTP→HTTPS redirect (the 80→443 `return 301` is already in the commented block).

**Files to touch:** `nginx/nginx.conf`, `docker-compose.yml`, `.env`, DNS.
**Commands:** `sudo certbot certonly --standalone -d api.<domain> && docker compose restart nginx`.
**Verify:** `curl -I https://api.<domain>/health` → `200` + HSTS header. Test SSL Labs (A grade).
**Gotchas:** Certbot renewal — add a cron / `certbot renew --deploy-hook "docker compose restart nginx"`. If you used the named volume `nginx_certs` before, switch to the bind mount so certs update live.

### P0-6 — Real ZarinPal payment (replace mock) · `M`

- [ ] Done

**Evidence:** `app/services/subscriptions.py:67-94` returns a fake `authority` + sandbox URL; `:165` stores `mock_ref_id`. No `payment_service.py` exists. `scale_plan` Session F (unchecked). Users can "buy" premium for free right now.

**Fix (step-by-step):**
1. Create `app/services/payment_service.py` (follow `scale_plan` Section 10):
   ```python
   import httpx
   from app.core.config import settings

   ZARINPAL_REQUEST = "https://api.zarinpal.com/pg/v4/payment/request.json"
   ZARINPAL_VERIFY  = "https://api.zarinpal.com/pg/v4/payment/verify.json"
   ZARINPAL_START   = "https://www.zarinpal.com/pg/StartPay/{authority}"

   class PaymentError(Exception): ...

   class ZarinpalService:
       async def create(self, amount_toman: int, description: str, callback_url: str) -> dict:
           async with httpx.AsyncClient(timeout=15) as c:
               r = await c.post(ZARINPAL_REQUEST, json={
                   "merchant_id": settings.ZARINPAL_MERCHANT_ID,
                   "amount": amount_toman * 10,          # toman → rial
                   "description": description,
                   "callback_url": callback_url,
               })
           data = r.json()
           if data.get("data", {}).get("code") != 100:
               raise PaymentError(str(data.get("errors")))
           authority = data["data"]["authority"]
           return {"authority": authority, "payment_url": ZARINPAL_START.format(authority=authority)}

       async def verify(self, amount_toman: int, authority: str) -> str:
           async with httpx.AsyncClient(timeout=15) as c:
               r = await c.post(ZARINPAL_VERIFY, json={
                   "merchant_id": settings.ZARINPAL_MERCHANT_ID,
                   "amount": amount_toman * 10,
                   "authority": authority,
               })
           data = r.json()
           code = data.get("data", {}).get("code")
           if code not in (100, 101):
               raise PaymentError(str(data.get("errors")))
           return str(data["data"]["ref_id"])
   ```
2. In `/subscriptions/purchase`: call `ZarinpalService.create(...)`, then store the intent in Redis so the callback can be trusted:
   ```python
   await redis_client.setex(f"purchase:{authority}", 3600, json.dumps({
       "user_id": str(current_user.id), "plan": body.plan_id, "amount_toman": price
   }))
   return PurchaseResponse(redirect_url=url, authority=authority)
   ```
3. In `/subscriptions/verify` (GET callback): read `authority` + `Status` query param; **verify the intent from Redis** (prevents a user from forging an authority); call `ZarinpalService.verify(...)`; only then grant premium via `RewardService.grant_premium_days(...)`.
4. Set real `ZARINPAL_MERCHANT_ID` in `.env`, `ZARINPAL_SANDBOX=false` for prod, and a real `ZARINPAL_CALLBACK_URL=https://api.<domain>/api/v1/subscriptions/verify`.

**Files to touch:** new `app/services/payment_service.py`, `app/api/v1/endpoints/subscriptions.py`, `app/services/subscriptions.py`, `.env`.
**Commands:** `pytest tests/done/test_subscriptions.py -v` (add a test that mocks httpx to assert verify-on-fake-authority fails).
**Verify:** In sandbox, walk through purchase → redirect → callback → premium granted. Try replaying the callback → should be idempotent (delete the Redis key after verify).
**Gotchas:**
- ZarinPal v4 wants amounts in **Rials** (toman × 10). The current `subscriptions.py` mixes both — standardize.
- The callback is a browser redirect (GET), so the user can hit it twice (refresh). Make verify idempotent: delete the Redis intent key after success; reject re-verify of a consumed authority.

### P0-7 — Real email sending (verification + password-reset codes) · `M`

- [ ] Done

**Evidence:** `app/services/email_service.send_verification_code` is mocked in tests; in `auth.py` codes are only written to Redis and never sent. Users literally cannot register or reset passwords in prod. `scale_plan` lists email as ❌ Missing.

**Fix (step-by-step):**
1. Implement `app/services/email_service.py` with an async SMTP/SendGrid transport:
   ```python
   import aiosmtplib
   from email.message import EmailMessage

   async def send_verification_code(to_email: str, code: str):
       msg = EmailMessage()
       msg["From"] = settings.SMTP_FROM
       msg["To"] = to_email
       msg["Subject"] = "Bondi — your verification code"
       msg.set_content(f"Your code is: {code}\nIt expires in 5 minutes.")
       await aiosmtplib.send(msg, hostname=settings.SMTP_HOST, port=587,
                             username=settings.SMTP_USER, password=settings.SMTP_PASS, start_tls=True)
   ```
2. Add `SMTP_HOST/USER/PASS/FROM` to `Settings` (`config.py`) and `.env`.
3. In `auth.py` `register/init` and `password-reset`: after `redis.store_verification_code(...)`, fire the email **off the request path** — via `BackgroundTasks` now, or Celery once P1-7 is done — so a slow SMTP server doesn't block registration.
4. Keep the existing test mock (`mock_email_service` patches `send_verification_code`) so tests stay deterministic.

**Files to touch:** `app/services/email_service.py`, `app/api/v1/endpoints/auth.py`, `app/core/config.py`, `.env`.
**Commands:** `pytest tests/done/test_auth.py -v`.
**Verify:** Register with a real inbox → receive the code email within seconds. Confirm wrong-code attempts don't leak the correct code.
**Gotchas:**
- Iranian mail delivery to Gmail can be unreliable / rate-limited. Consider a transactional provider (Mailgun/Postmark) with a non-Iranian SMTP relay.
- Never block the HTTP response on SMTP — always background-send and fail open (log if send fails; the code is still in Redis so the user can retry).

### P0-8 — Rotate production secrets + document ENCRYPTION_SECRET rotation · `XS`

- [ ] Done

**Evidence:** `.env:10` `SECRET_KEY=change-this-to-64-random-characters-in-production`, `.env:23` `ADMIN_SECRET_KEY=change-this-to-random-string`, `.env:84` a real `ENCRYPTION_SECRET`, `.env:125` a real `GLITCHTIP_SECRET_KEY`. No re-encryption script exists. `security_plan` Section 6 (unchecked).

**Why:** Placeholder `SECRET_KEY` means anyone can forge JWTs. Rotating `ENCRYPTION_SECRET` without re-encrypting **bricks every existing chat** (all `messages.content` becomes undecryptable).

**Fix (step-by-step):**
1. Generate real secrets on the server:
   ```sh
   openssl rand -hex 32   # SECRET_KEY
   openssl rand -hex 16   # ADMIN_SECRET_KEY
   openssl rand -hex 16   # ENCRYPTION_SECRET (new)
   openssl rand -hex 32   # GLITCHTIP_SECRET_KEY
   ```
2. Put them in the **server's** `.env` (never commit). Keep `.env.example` with placeholders only.
3. Write `scripts/rotate_encryption_secret.py`:
   ```python
   # Pseudocode — run as a one-off, with app stopped or in read-only mode.
   # 1. Set OLD_SECRET = current ENCRYPTION_SECRET in env.
   # 2. Load all messages, decrypt with OLD key, re-encrypt with NEW key, UPDATE in batches.
   # 3. Swap ENCRYPTION_SECRET to NEW in .env, restart app.
   # Key point: derive_chat_key() takes the secret from settings, so just re-encrypt the
   # content column: old = decrypt(content, old_secret), new = encrypt(plaintext, new_secret).
   ```
4. Document the dual-key window in `docs/security_plan.md` (Section 6.3): during rotation, support both old+new secrets for a short read window, then cut over.

**Files to touch:** server `.env`, new `scripts/rotate_encryption_secret.py`, `docs/security_plan.md`.
**Commands:** `python -m scripts.rotate_encryption_secret --dry-run` then `--apply`.
**Verify:** After rotation, load an old chat → messages still decrypt. Forge a JWT with the old `SECRET_KEY` → 401.
**Gotchas:** Rotate `SECRET_KEY` first (cheap — just forces re-login). Rotate `ENCRYPTION_SECRET` only after the re-encrypt script is written and tested on a DB backup. **Always take a `pg_dump` before rotating.**

---

## 🟠 P1 — Must before scaling (100+ concurrent users)

### P1-1 — FCM push send is synchronous/blocking inside async code · `S` · NEW

- [x] Done

**Evidence:** `app/services/push_service.py:62` calls `messaging.send_each_for_multicast(message)` (a **blocking** HTTP call to Google FCM v1) directly inside an `async` function — no `run_in_executor`. It's called from `notification_service.py` inside `BackgroundTasks`.

**Why:** One FCM call blocks the worker's event loop for its full HTTP round-trip (100–500 ms). A burst of matches/likes → the event loop stalls for *all* users on that worker, not just the ones getting a push.

**Fix (step-by-step):**
1. Wrap the blocking call in a thread:
   ```python
   import asyncio
   # push_service.py
   response = await asyncio.to_thread(messaging.send_each_for_multicast, message)
   ```
   (`asyncio.to_thread` is the modern `run_in_executor(None, ...)` shortcut.)
2. Better: once P1-7 lands, push entirely to a Celery task so a failed FCM call can retry and never touches the request worker.

**Files to touch:** `app/services/push_service.py`.
**Commands:** `pytest tests/done/ -k push -v` (tests mock FCM; they'll still pass).
**Verify:** Load-test 50 simultaneous matches → event-loop latency (p99) stays flat instead of spiking during the FCM bursts.
**Gotchas:** `firebase_admin` is global-initialized (`push_service.py:13-29`) and not thread-safe to re-init. Keep the single `_initialize_firebase()` guard; just offload the *send* call, not the init.

### P1-2 — Discover/Search generates a presigned MinIO URL per photo per row · `M` · NEW

- [ ] Done

**Evidence:** `discover.py:175-178` and `search.py:271-274` do `[await PhotoService.get_photo_url(p.url, p.status) for p in approved_photos_raw]`. `photo_service.py:212-218` runs a synchronous `generate_presigned_url` for every non-approved photo. A page of 20 users × 9 photos = up to **180 serialized S3 calls** per discover/search request.

**Why:** Makes discover feel sluggish and hammers MinIO. Approved photos are fine (plain public URL, no signing); the cost is all on pending/private photos.

**Fix (step-by-step):**
1. **Stop expanding all photos on the swipe deck.** The card only needs `main_photo_url`. Change the discover/search response builders to resolve only the main photo; keep the full photo list for the profile screen (a separate, cached call).
2. **Cache presigned URLs** — they're valid 15 min (`S3_SIGNED_URL_EXPIRE_SECONDS=900`) so caching for 5 min is safe:
   ```python
   # photo_service.py
   async def get_photo_url(key: str, status: str) -> str:
       if status in PUBLIC_STATUSES:
           return f"{settings.S3_PUBLIC_BASE_URL}/{key}"
       cache_key = f"presign:{key}"
       cached = await redis_client.get(cache_key)
       if cached:
           return cached
       async with _s3_client() as s3:
           url = await s3.generate_presigned_url(...)
       await redis_client.setex(cache_key, 300, url)   # 5 min
       return url
   ```
3. Even better: use a single `MGET` to batch all presigned URLs for a page instead of N round-trips.

**Files to touch:** `app/services/photo_service.py`, `app/api/v1/endpoints/discover.py`, `app/api/v1/endpoints/search.py`, `app/schemas/discover.py` (response may only carry `main_photo_url` on the deck).
**Commands:** `pytest tests/done/test_discover.py tests/done/test_search.py -v`.
**Verify:** Time `GET /discover?limit=20` — should drop dramatically. Redis `KEYS presign:*` shows cached URLs.
**Gotchas:** If a photo's moderation status flips pending→approved, the cached presigned URL for the private version lingers 5 min — acceptable, but invalidate on `publish_photo` (`admin_photos.py`).

### P1-3 — Redis password + MinIO non-default credentials · `XS`

- [ ] Done

**Evidence:** `docker-compose.yml:62-74` Redis has no `requirepass`; `:82-83` MinIO `minioadmin/minioadmin`. `security_plan` Session D (unchecked). Anyone on the Docker network (or host, since 6379/9000 are published) can read/write cache + tokens + photos.

**Fix (step-by-step):**
1. Redis: add a password and use it in the URL:
   ```yaml
   redis:
     command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
   app:
     environment:
       REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379
   ```
2. MinIO: set strong root creds from `.env`:
   ```yaml
   minio:
     environment:
       MINIO_ROOT_USER: ${MINIO_ROOT_USER}
       MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
   ```
   `minio-init` and `mc alias set` must use the same creds.
3. Add `REDIS_PASSWORD`, `MINIO_ROOT_USER/PASSWORD` to `.env.example` (as placeholders) and the real `.env`.
4. In prod, **remove** the published `ports:` for redis (6379) and minio (9000/9001) — internal-only (pairs with P1-4).

**Files to touch:** `docker-compose.yml`, `docker-compose.test.yml` (keep test creds simple), `.env`, `.env.example`.
**Commands:** `docker compose up -d redis minio && docker compose logs redis minio`.
**Verify:** `redis-cli -h localhost -p 6379 ping` → `NOAUTH Authentication required`. MinIO console login rejects `minioadmin`.
**Gotchas:** GlitchTip also uses Redis (`redis://redis:6379/1`) — add the password there too (`:130,152` in compose). `slowapi` (`limiter.py`) reads `settings.REDIS_URL` so it picks up the password automatically.

### P1-4 — Docker network isolation · `S`

- [ ] Done

**Evidence:** `docker-compose.yml` uses the default bridge; db/redis/minio ports are published to the host. `security_plan` Section 9.1 (unchecked).

**Fix (step-by-step):**
1. Define two networks in `docker-compose.yml`:
   ```yaml
   networks:
     internal:
       driver: bridge
     frontend:
       driver: bridge
   ```
2. Put `db`, `redis`, `minio`, `pgbouncer` on `internal` only. Put `nginx` on `frontend` only. Put `app` (and `glitchtip`) on **both** so it can talk to both sides.
3. Remove `ports:` from `db`, `redis`, `minio`, `pgbouncer` in prod compose (keep them only in a dev override file).

**Files to touch:** `docker-compose.yml`, optionally a `docker-compose.override.yml` for dev ports.
**Commands:** `docker network inspect dating_internal | grep -i container`.
**Verify:** From the host, `nc -z localhost 6379` should fail in prod. From inside `app`, `redis-cli ping` works.
**Gotchas:** Don't put GlitchTip on `internal` if it needs to receive events from outside — but it talks to `redis` and `db`, so `internal` (+ outbound only) is fine.

### P1-5 — `revoke_all_user_tokens` is O(N) over ALL refresh tokens · `S` · NEW

- [ ] Done

**Evidence:** `app/core/redis.py:87-101` does `scan_iter(match="refresh_token:*")` then `GET` + `DEL` per key on every password change / ban. It scans **every** refresh token in Redis.

**Why:** With 100k+ users (and 30-day token TTLs, so many live tokens) this is a multi-second scan that blocks the password-change request and spikes Redis CPU — on a hot security path.

**Fix (step-by-step):**
1. Maintain a per-user set of token ids alongside the token:
   ```python
   async def store_refresh_token(token, user_id):
       jti = secrets.token_urlsafe(16)
       key = f"refresh_token:{user_id}:{jti}"
       await redis_client.set(key, user_id, ex=REFRESH_TOKEN_TTL)
       await redis_client.sadd(f"user_tokens:{user_id}", jti)
       await redis_client.expire(f"user_tokens:{user_id}", REFRESH_TOKEN_TTL)
       return jti   # store jti in the JWT too
   ```
2. Revoke only that user's tokens (no global scan):
   ```python
   async def revoke_all_user_tokens(user_id):
       jtis = await redis_client.smembers(f"user_tokens:{user_id}")
       pipe = redis_client.pipeline()
       for jti in jtis:
           pipe.delete(f"refresh_token:{user_id}:{jti}")
       pipe.delete(f"user_tokens:{user_id}")
       await pipe.execute()
   ```
3. On single-token logout (`revoke_refresh_token`), remove the matching jti from the set too.

**Files to touch:** `app/core/redis.py`, `app/core/security.py` (jti already generated — reuse it), `auth.py` (store/refresh/logout).
**Commands:** `pytest tests/done/test_auth.py -v`.
**Verify:** Change password with 50k tokens in Redis → completes in <50 ms (was seconds).
**Gotchas:** The set `user_tokens:{user_id}` must have a TTL so it doesn't leak after the last token expires. Re-set the TTL on each `store_refresh_token`.

### P1-6 — Verify per-(user,match) rate limit is not IP-only · `S` · NEW

- [ ] Done

**Evidence:** `messages.py` uses `@limiter.limit("30/minute")` (SlowAPI keys on IP by default via `get_remote_address`). `security_plan` claims "per-match 30/min" but no `(user_id, match_id)` keying is visible.

**Why:** Two users behind one NAT share a bucket → one throttles the other. A single abusive user on a clean IP is only IP-throttled, not per-match throttled — they can spam N matches at 30/min each.

**Fix (step-by-step):**
1. Enforce a Redis token bucket per `(user_id, match_id)` directly in the send path (more reliable than a SlowAPI key func, which runs before dependency injection):
   ```python
   bucket = int(time.time() // 60)
   key = f"rl:msg:{user_id}:{match_id}:{bucket}"
   count = await redis_client.incr(key)
   if count == 1:
       await redis_client.expire(key, 60)
   if count > 30:
       raise HTTPException(429, "Too many messages to this match. Try again shortly.")
   ```
2. Optionally add a SlowAPI custom key func for the global 30/min too, but the Redis bucket is the real per-match throttle.

**Files to touch:** `app/api/v1/endpoints/messages.py` (send endpoint), maybe `app/core/limiter.py`.
**Commands:** `pytest tests/done/test_messages*.py -v` (add a test: 31st message to the same match → 429).
**Verify:** Send 31 messages to one match fast → 429 on the 31st. Send 1 message to a different match → 200.
**Gotchas:** Make sure `user_id` and `match_id` are both resolved **before** the bucket check (they are, in the existing send flow). Count the bucket by minute-epoch so it self-expires.

### P1-7 — No durable task queue (Celery installed but 100% dead code) · `M` · NEW

- [ ] Done

**Evidence:** `requirements.txt` pins `celery`, `kombu`, `billiard`, `amqp`, but `app/tasks/*.py` are all **0 bytes** and `grep celery @task delay apply_async` returns nothing. No worker/beat service in `docker-compose.yml`. All async work uses in-process `BackgroundTasks`.

**Why:** `BackgroundTasks` run in the request's worker process — **lost on crash/restart**, no retries, no scheduling. A dating app needs: scheduled premium expiry, scheduled cleanup, retry of failed FCM/email, "you haven't been online" nudges. None of that can run reliably today. The current half-state (deps present, code absent) is the worst of both — image bloat + no capability.

**Fix — pick ONE path:**

**Path A (recommended): wire up Celery**
1. Create `app/tasks/celery_app.py`:
   ```python
   from celery import Celery
   from app.core.config import settings
   celery_app = Celery("bondi", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
   celery_app.conf.update(
       task_acks_late=True,
       task_reject_on_worker_lost=True,
       task_default_queue="bondi",
       broker_transport_options={"visibility_timeout": 3600},
   )
   ```
2. Move push/email/match-notification into `app/tasks/notifications.py` as `@celery_app.task` with `autoretry_for=(Exception,), retry_backoff=True, max_retries=3`.
3. Add a `celery-worker` and `celery-beat` service in `docker-compose.yml`:
   ```yaml
   celery-worker:
     build: .
     command: celery -A app.tasks.celery_app worker -l info --concurrency=4
     env_file: .env
     depends_on: [redis]
   celery-beat:
     build: .
     command: celery -A app.tasks.celery_app beat -l info
     env_file: .env
     depends_on: [redis]
   ```
4. Add a beat schedule for: daily premium-expiry sweep, nightly stale-session cleanup, retry-dead-letter flush, "inactive user" nudges.
5. Replace `BackgroundTasks` callers with `task.delay(...)`.

**Path B (minimal): remove Celery, accept BackgroundTasks limits**
- Delete celery/kombu/billiard/amqp from `requirements.txt` and the empty `app/tasks/*.py`. Document that notifications are fire-and-forget in-process (acceptable for early MVP, but plan Path A before real scale).

**Files to touch (Path A):** new `app/tasks/celery_app.py`, `app/tasks/notifications.py`, `docker-compose.yml`, callers in `swipes.py`/`messages.py`/`notification_service.py`, `requirements.txt` (keep celery).
**Commands:** `celery -A app.tasks.celery_app inspect active` (worker reachable).
**Verify:** Kill the app container mid-match-notification → the queued Celery task still completes. Trigger a beat job → premium expires on schedule.
**Gotchas:**
- Celery tasks are sync by default — wrap any async DB/Redis code with `asyncio.run(...)` or `asgiref.sync.async_to_sync`.
- Beat is a singleton; run **exactly one** `celery-beat` container.
- Redis broker password (P1-3) must be in the broker URL.
- Pair with P2-7 (NSFW ML) — heavy inference must run in the worker, never the request path.

---

## 🟡 P2 — Backend hardening / correctness

### P2-1 — Daily-limit "midnight" uses server-local TZ, not Tehran · `S` · NEW

- [ ] Done

**Evidence:** `app/services/reward_service.py:19-24` `_seconds_until_midnight()` uses `datetime.now()` (naive → server-local TZ). `models/daily_limit.py:13` comment says `Tehran date (UTC+3:30)` but `date.today()` is also server-local. If the server runs UTC (Docker default), limits reset at 03:30 Tehran time, not midnight.

**Why:** Users see "0 likes remaining" until 03:30 AM their time — confusing and a support burden. Also the Redis TTL (until "midnight") and the DB `date` column can disagree on which "today" is.

**Fix (step-by-step):**
1. Pin a single timezone everywhere daily limits are computed:
   ```python
   from zoneinfo import ZoneInfo
   TEHRAN = ZoneInfo("Asia/Tehran")
   def _tehran_now(): return datetime.now(TEHRAN)
   def _tehran_today(): return _tehran_now().date()
   def _seconds_until_midnight():
       now = _tehran_now()
       midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
       return int((midnight - now).total_seconds())
   ```
2. Replace `date.today()` in `reward_service.py` and `chat_service.py` with `_tehran_today()`.
3. Store the Tehran date in `daily_limits.date` (already the intent).

**Files to touch:** `app/services/reward_service.py`, `app/services/chat_service.py`, any other `date.today()` in limit paths.
**Commands:** `grep -rn 'date.today()\|datetime.now()' app/` then `pytest tests/done/ -k limit -v`.
**Verify:** Set server TZ to UTC; a like at 23:00 Tehran → `daily_limits.date` is today-Tehran, TTL ~1h.
**Gotchas:** If a user travels, their "today" is still server-Tehran by design — that's the product decision (limits are per-calendar-day-Tehran). Don't use the user's device time.

### P2-2 — Naive `datetime.utcnow()` mixed with timezone-aware datetimes · `XS` · NEW

- [ ] Done

**Evidence:** `app/services/chat_service.py:354` `read_at=datetime.utcnow()`, `mark_messages_as_delivered` similarly. Everywhere else uses `datetime.now(timezone.utc)`.

**Why:** Postgres `DateTime(timezone=True)` + a naive UTC value can attach the server's local time or raise on some drivers; can cause subtle "message read 3.5h ago" bugs or wrong sort order across DST/server moves.

**Fix (step-by-step):**
1. Find all occurrences: `grep -rn 'utcnow()\|datetime.now()' app/`.
2. Replace `datetime.utcnow()` → `datetime.now(timezone.utc)`.
3. Replace bare `datetime.now()` with `datetime.now(timezone.utc)` for anything stored in a tz column (or with `ZoneInfo` if local — see P2-1).

**Files to touch:** `app/services/chat_service.py`, and any hit from the grep.
**Commands:** `grep -rn 'utcnow()' app/` (should return nothing after).
**Verify:** `pytest tests/done/test_messages*.py -v`. Mark a message read → `read_at` is tz-aware in the DB.
**Gotchas:** None — this is a pure, mechanical improvement.

### P2-3 — Unmatched-chat path has no block check + spam vector · `M` · NEW

- [ ] Done

**Evidence:** `app/api/v1/endpoints/messages.py:42-72` `get_match_or_chat` lets a user message **any** active user by UUID (the "unmatched chat" feature). `chat_service.check_unmatched_message_limit` only caps at 2 messages before the recipient must accept. No block check visible on this unmatched path.

**Why:** A user can initiate a chat with any user ID they know/guess — harassment vector. Also no recipient block enforcement on this path, so a blocked user can still send the 2 unmatched messages.

**Fix (step-by-step):**
1. In `get_match_or_chat` (or before creating an unmatched message), add a **bidirectional block check**:
   ```python
   blocked = await session.execute(
       select(Block).where(
           or_(
               and_(Block.blocker_id == user_id, Block.blocked_id == identifier),
               and_(Block.blocker_id == identifier, Block.blocked_id == user_id),
           )
       )
   )
   if blocked.scalar_one_or_none():
       raise HTTPException(403, "Cannot message this user")
   ```
2. Gate unmatched-chat creation: only allow it if the sender has a **like** toward the recipient (i.e., came from discover/swipes), not arbitrary UUIDs. Add a `Swipe(direction="like")` existence check.
3. Add a per-target rate limit (Redis bucket per `(sender, receiver)`) so a user can't mass-open unmatched chats.

**Files to touch:** `app/api/v1/endpoints/messages.py`, `app/services/chat_service.py`, `app/models/block.py`.
**Commands:** `pytest tests/done/test_messages*.py tests/done/test_blocks.py -v` (add tests: blocked user can't unmatched-chat; arbitrary UUID → 403/404).
**Verify:** Block user B, then try to unmatched-message them as A → 403.
**Gotchas:** Don't leak whether the block exists via timing — return the same 403/404 as "user not found" to avoid enumeration.

### P2-4 — Encryption failure stores plaintext silently · `S` · NEW

- [ ] Done

**Evidence:** `app/models/message.py:83-91` setter: `try: self._content = encrypt_message(...) except: self._content = value` (stores **plaintext** on any error). Getter (`:74-76`) returns the ciphertext blob on decrypt failure → client gets garbage with no alert.

**Why:** If encryption ever fails (transient error, misconfigured secret), the message is stored **unencrypted** silently — a data-confidentiality regression with zero visibility.

**Fix (step-by-step):**
1. Setter: on `encrypt_message` failure, **raise** (let the request fail loudly) + log:
   ```python
   @content.setter
   def content(self, value):
       if value and self.match_id:
           try:
               self._content = encrypt_message(value, str(self.match_id))
           except Exception as e:
               logger.exception("encrypt_failed", match_id=str(self.match_id))
               raise RuntimeError("Failed to encrypt message") from e
       else:
           self._content = value
   ```
2. Getter: on decrypt failure, log to GlitchTip and return a safe placeholder string (e.g. `"[undecryptable]"`), never the raw ciphertext blob.

**Files to touch:** `app/models/message.py`.
**Commands:** `pytest tests/done/test_messages_encryption.py -v`.
**Verify:** Temporarily set a bad `ENCRYPTION_SECRET` → sending a message fails loudly (500/log), no plaintext row in DB.
**Gotchas:** This is a fail-closed change — make sure no existing code path relies on the silent plaintext fallback (none should). Test the rotation script (P0-8) still works with the new raising setter.

### P2-5 — `is_verified` inconsistent between discover and search · `XS` · NEW

- [ ] Done

**Evidence:** `discover.py:235` uses `user.phone_verified`; `search.py:322` uses `profile.is_verified`. Two different definitions of the verified badge shown to users on the same app.

**Why:** A user can appear verified in one list and not in another. For a dating app where the blue badge drives trust, inconsistency erodes it.

**Fix (step-by-step):**
1. Decide on one source of truth. `profile.is_verified` (set by face verification flow) is the right one — `phone_verified` only means the phone is real, not the person.
2. Replace `discover.py:235` `user.phone_verified` → `profile.is_verified if profile.is_verified is not None else False` (matching `search.py:322`).
3. Add a small helper `verified_badge(profile) -> bool` and use it in both.

**Files to touch:** `app/api/v1/endpoints/discover.py`, `app/api/v1/endpoints/search.py`, maybe `app/schemas/discover.py`.
**Commands:** `pytest tests/done/test_discover.py tests/done/test_search.py -v`.
**Verify:** A face-verified user shows the badge in both discover and search; a non-verified shows it in neither.
**Gotchas:** If `phone_verified` is the product's intended "verified" meaning for now, flip both to it instead — just pick one and use it everywhere.

### P2-6 — Cache helpers swallow all exceptions silently · `XS` · NEW

- [ ] Done

**Evidence:** `app/core/cache.py:65-66, 77-78, 101, 113, 120, 132, 142` — every cache op is `try/except: pass`.

**Why:** If Redis is down you silently fall back to the DB path (good for availability) but get **zero visibility** into the degradation — you'll only notice when the DB melts.

**Fix (step-by-step):**
1. In each except block, log a warning with the key and error so GlitchTip catches it:
   ```python
   except Exception as e:
       logger.warning("cache_get_failed", key=key, error=str(e))
       return None
   ```
2. Keep the silent fallback (don't raise) — availability is more important than cache freshness.

**Files to touch:** `app/core/cache.py`.
**Commands:** `pytest tests/done/ -q`.
**Verify:** Stop Redis → hit `/interests` → GlitchTip shows `cache_get_failed` warnings; the endpoint still works (DB fallback).
**Gotchas:** Use `logger.warning`, not `error`, so you don't page on-call for every cache miss during a Redis blip. Add an alert on a high rate of `cache_*_failed`.

### P2-7 — NSFW detection is a skin-tone heuristic (fairness + accuracy risk) · `L` · NEW

- [ ] Done

**Evidence:** `app/services/nsfw_service.py:96-134` maps "skin pixel ratio" → NSFW score. The doc itself says "swap for ML model in production." `nsfw_service.check_image` runs synchronously on the event loop in the upload path (`photos.py:80`).

**Why:** Skin-tone heuristics **false-positive on darker skin tones** — a serious fairness/racism problem for a dating app, and a reputational/legal risk. They also **false-negative** on non-skin-colored explicit content. Threshold 0.8 still rejects normal beach/portrait photos of light-skinned people. Plus the numpy/PIL work blocks the event loop.

**Fix (step-by-step):**
1. Replace the heuristic with `opennsfw2` (or a TF/ONNX model). Keep the `check_image` signature.
2. Run inference in a thread executor (it's CPU/GPU-bound):
   ```python
   async def check_image(self, file_bytes: bytes):
       if not self._enabled: return True, 0.0
       return await asyncio.to_thread(self._classify_ml, file_bytes)
   ```
3. Keep the quarantine flow (`quarantine_photo`) for the rejected images.
4. Calibrate the new threshold against a labeled set before flipping it on in prod.
5. If even the ML model is too slow on CPU, move NSFW into the Celery worker (P1-7) — accept the photo as `pending`, then flip to `approved`/`rejected` after the async check.

**Files to touch:** `app/services/nsfw_service.py`, `app/api/v1/endpoints/photos.py`, `requirements.txt` (`opennsfw2` or `onnxruntime` + model).
**Commands:** `pytest tests/done/ -k nsfw -v`.
**Verify:** Upload a set of diverse skin-tone portraits → none auto-rejected for NSFW. Upload actual NSFW → rejected.
**Gotchas:** `opennsfw2` pulls TensorFlow (heavy image). Prefer an ONNX variant to keep the image lean (you already have `onnxruntime` pinned). Validate the model on your actual user-photo distribution before trusting the threshold.

### P2-8 — Nominatim reverse-geocode has no rate limit + weak User-Agent · `S` · NEW

- [ ] Done

**Evidence:** `app/services/location_service.py:290-303` calls `https://nominatim.openstreetmap.org/reverse` with `User-Agent: DatingApp/1.0`. Nominatim's usage policy requires a valid contact User-Agent and limits heavy use to ~1 req/sec; it will IP-ban at scale.

**Why:** Every GPS update that triggers reverse geocoding hits the public OSM service. A few hundred users moving → instant ban → all location text breaks.

**Fix (step-by-step):**
1. Self-host a Nominatim (or Photon) instance with an `iran-latest.osm.pbf` extract, OR use a paid geocoder (Google/Mapbox) with a quota.
2. Add a Redis token-bucket rate limiter around the call (1 req/sec global, plus per-user):
   ```python
   allowed = await redis_client.set(f"rl:nominatim:global", "1", ex=1, nx=True)
   if not allowed: return cached_or_none
   ```
3. Use a real contact User-Agent: `Bondi <admin@yourdomain>`.
4. The 24h cache (`reverse_geocode` already caches) is good — keep it.

**Files to touch:** `app/services/location_service.py`, `docker-compose.yml` (if self-hosting Nominatim).
**Commands:** `pytest tests/done/test_locations.py -v`.
**Verify:** Hammer the location update endpoint → no 429/ban from OSM; results come from cache after the first.
**Gotchas:** Self-hosted Nominatim needs ~30–70 GB RAM to build the planet; use the Iran extract (much smaller) or Photon (Elasticsearch) which is lighter.

### P2-9 — `get_notifications` count wraps the full ordered query · `XS` · NEW

- [ ] Done

**Evidence:** `app/api/v1/endpoints/notifications.py:43-45` does `select(func.count()).select_from(query.subquery())` where `query` already has `ORDER BY created_at DESC`. Wrapping an ordered query in a count subquery is wasteful (the planner may materialize the sort).

**Fix (step-by-step):**
1. Build a separate count query with no ORDER BY:
   ```python
   count_query = select(func.count(Notification.id)).where(Notification.user_id == current_user.id)
   total = await session.scalar(count_query)
   ```
2. Apply `ORDER BY` + `offset/limit` only on the data query.

**Files to touch:** `app/api/v1/endpoints/notifications.py`.
**Commands:** `pytest tests/done/test_notifications.py -v`.
**Verify:** `EXPLAIN` the count query → no Sort node.
**Gotchas:** Minor but it's a free win. The same pattern recurs in `matches.py` (P2-10) and `swipes.py` liked/likers.

### P2-10 — `get_matches` count wraps an eager-loaded query · `XS` · NEW

- [ ] Done

**Evidence:** `app/api/v1/endpoints/matches.py:52` `count_query = select(func.count()).select_from(query.subquery())` where `query` has 4 `selectinload` chains (`:39-43`). Counting shouldn't load relationships.

**Fix (step-by-step):**
1. Count on a plain query without the `selectinload` options:
   ```python
   base_filter = or_(Match.user1_id == current_user.id, Match.user2_id == current_user.id), Match.is_active == True
   total = await session.scalar(select(func.count(Match.id)).where(*base_filter))
   # data query keeps the selectinload chains + ORDER BY + offset/limit
   ```
2. Apply the same fix to `swipes.py` `get_liked`/`get_likers` count subqueries (`:457-468`).

**Files to touch:** `app/api/v1/endpoints/matches.py`, `app/api/v1/endpoints/swipes.py`.
**Commands:** `pytest tests/done/test_matches.py tests/done/test_swipes.py -v`.
**Verify:** `EXPLAIN ANALYZE` the count → no join to `user_profiles`/`photos`.
**Gotchas:** Make sure the count filter matches the data query's filter exactly (same `is_active`, same user conditions) or the total/next_offset will drift.

### P2-11 — `PhotoService.MAX_FILE_SIZE` hardcoded, ignores config · `XS` · NEW

- [ ] Done

**Evidence:** `app/services/photo_service.py:32` `MAX_FILE_SIZE = 5 * 1024 * 1024` (hardcoded 5MB) but `settings.MAX_PHOTO_SIZE_MB=10` exists (`config.py:76`) and is ignored.

**Fix (step-by-step):**
1. Compute the limit from settings. Because class attributes evaluate at class-definition time and `settings` is importable then, this works:
   ```python
   MAX_FILE_SIZE = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
   ```
2. If you want runtime-overridable, make it a `@staticmethod` reading `settings` on each call.

**Files to touch:** `app/services/photo_service.py`.
**Commands:** `pytest tests/done/test_photos.py -v`.
**Verify:** Upload a 7MB photo with `MAX_PHOTO_SIZE_MB=10` → accepted (was rejected).
**Gotchas:** Make sure the nginx `client_max_body_size` (P2-13) is at least as large, or nginx 413s before the app's check runs.

### P2-12 — `media_service` creates a new `aioboto3.Session()` per call · `XS` · NEW

- [ ] Done

**Evidence:** `app/services/media_service.py:55,74,114,129,162` each does `import aioboto3; aioboto3.Session().client(...)`. `photo_service.py:15` already has a shared `_s3_session`.

**Fix (step-by-step):**
1. Extract the shared client factory to a common module (e.g., `app/services/storage.py` — currently empty!) and use it everywhere:
   ```python
   # app/services/storage.py
   import aioboto3
   from app.core.config import settings
   _session = aioboto3.Session()
   def s3_client():
       return _session.client("s3", endpoint_url=settings.S3_ENDPOINT_URL,
           aws_access_key_id=settings.S3_ACCESS_KEY,
           aws_secret_access_key=settings.S3_SECRET_KEY, region_name=settings.S3_REGION)
   ```
2. Replace the per-call `aioboto3.Session()` in `media_service.py` and `nsfw_service.py:quarantine_photo` with `from app.services.storage import s3_client`.

**Files to touch:** new `app/services/storage.py`, `app/services/media_service.py`, `app/services/photo_service.py`, `app/services/nsfw_service.py`.
**Commands:** `pytest tests/done/ -q`.
**Verify:** Behavior unchanged; fewer connection churn logs in MinIO.
**Gotchas:** `storage.py` currently returns empty content — it's a placeholder file; repurpose it.

### P2-13 — No `client_max_body_size` in nginx · `XS` · NEW

- [ ] Done

**Evidence:** `nginx/nginx.conf` has no `client_max_body_size`. Face-verification videos can be up to 20MB (`config.py:149`), chat photos up to 5MB. nginx's default is 1MB → large uploads `413 Request Entity Too Large` before the app sees them.

**Fix (step-by-step):**
1. Add to the `http` block in `nginx/nginx.conf`:
   ```nginx
   client_max_body_size 25M;
   ```
2. If you want per-location limits, set a smaller one on `/api/v1/users/me/photos` (10M) and a larger one on the face-verification video endpoint (25M).

**Files to touch:** `nginx/nginx.conf`.
**Commands:** `docker compose restart nginx`.
**Verify:** `curl -X POST -F "file=@20mb_video.mp4" https://api.<domain>/...` passes nginx (reaches the app's own size check).
**Gotchas:** Must be ≥ the largest `MAX_*_SIZE_MB` in config. Pair with P2-11 so the app's and nginx's limits agree.

### P2-14 — GlitchTip shares the app's Postgres instance · `S` · NEW

- [ ] Done

**Evidence:** `docker-compose.yml:129` GlitchTip `DATABASE_URL: postgres://dating_user:dating_pass@db:5432/glitchtip` — same Postgres process as the app.

**Why:** Under load, GlitchTip's event inserts compete with app queries for the same Postgres connection budget/CPU. A bug storm can degrade the app itself (the error tracker making the app slower is a bad feedback loop).

**Fix (step-by-step):**
1. Spin up a second small Postgres for GlitchTip (or a separate database with its own user + connection limit):
   ```yaml
   glitchtip-db:
     image: postgis/postgis:15-3.3
     environment: { POSTGRES_USER: glitchtip, POSTGRES_PASSWORD: ${GLITCHTIP_DB_PASS}, POSTGRES_DB: glitchtip }
   glitchtip:
     environment:
       DATABASE_URL: postgres://glitchtip:${GLITCHTIP_DB_PASS}@glitchtip-db:5432/glitchtip
   ```
2. Alternatively set a `connection_limit` on the `dating_user` role for the glitchtip DB.

**Files to touch:** `docker-compose.yml`.
**Commands:** `docker compose up -d glitchtip-db glitchtip`.
**Verify:** `pg_stat_activity` for the app DB no longer shows GlitchTip connections.
**Gotchas:** GlitchTip needs its migrations run; the worker already does `python manage.py migrate` — keep that pointing at the new DB.

### P2-15 — No graceful shutdown / WebSocket drain on SIGTERM · `S` · NEW

- [ ] Done

**Evidence:** `entrypoint.sh:27` `exec uvicorn ... --workers $WORKERS` with no graceful-termination config. On `SIGTERM` (deploy/restart), in-flight requests and long-lived WebSocket connections can be killed mid-message.

**Fix (step-by-step):**
1. Add uvicorn graceful shutdown (≥0.30):
   ```sh
   exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $WORKERS \
     --timeout-graceful-termination 30
   ```
2. In the WebSocket loops (`chat.py`, `matches.py`), catch `asyncio.CancelledError` and send a `server_shutdown` frame before closing:
   ```python
   except asyncio.CancelledError:
       try: await websocket.send_text(json.dumps({"type": "server_shutdown"}))
       except Exception: pass
       raise
   ```
3. In `docker-compose.yml`, set `stop_grace_period: 30s` on the `app` service.

**Files to touch:** `entrypoint.sh`, `app/api/v1/websocket/chat.py`, `app/api/v1/websocket/matches.py`, `docker-compose.yml`.
**Commands:** `docker compose restart app` (watch logs).
**Verify:** During a rolling restart, connected clients get a `server_shutdown` then reconnect; no mid-send message loss.
**Gotchas:** `--timeout-graceful-termination` must be < the docker `stop_grace_period` so uvicorn can actually drain before SIGKILL.

### P2-16 — Referral claim raises 500 on concurrent race · `XS` · NEW

- [ ] Done

**Evidence:** `app/api/v1/endpoints/referrals.py:67-71` does SELECT-then-INSERT; `models/referral_reward.py:12` has `UniqueConstraint("invited_id")` so the DB **does** prevent double-grant (good), but the code doesn't catch `IntegrityError` → a concurrent claim returns 500 instead of a clean 400.

**Fix (step-by-step):**
1. Wrap the insert in `try/except IntegrityError`:
   ```python
   from sqlalchemy.exc import IntegrityError
   try:
       session.add(reward)
       await session.commit()
   except IntegrityError:
       await session.rollback()
       raise HTTPException(400, "Referral already claimed")
   ```
2. Move the `grant_premium_days` calls to **after** the reward insert succeeds, so you don't grant premium then fail on the reward row.

**Files to touch:** `app/api/v1/endpoints/referrals.py`.
**Commands:** `pytest tests/done/test_referrals.py -v` (add: two concurrent claims → one 400, one 200, only one premium grant).
**Verify:** Two near-simultaneous claims → one succeeds, one clean 400, no 500, no double premium.
**Gotchas:** The ordering fix is important — don't grant premium before the reward row commits, or a failed reward leaves a dangling subscription.

### P2-17 — Celery in requirements but unused (dead dependency) · `XS` · NEW

- [ ] Done

**Evidence:** `requirements.txt` pins celery/kombu/billiard/amqp; `app/tasks/*.py` are 0 bytes; no worker in compose.
**Fix:** Either wire it up (P1-7 Path A) or remove the pins (`celery`, `kombu`, `billiard`, `amqp`, `vine`) from `requirements.txt` and delete the empty `app/tasks/` files. Dead deps bloat the image and confuse contributors.
**Files to touch:** `requirements.txt`, `app/tasks/` (if removing).
**Commands:** `pip install -r requirements.txt && python -c "import app.main"`.
**Verify:** Image size drops; `pip list | grep celery` returns nothing (if removing).
**Gotchas:** Don't remove if you plan P1-7 Path A immediately — leave a `# TODO P1-7` comment then.

## 🟢 P3 — Security hardening

### P3-1 — WebSocket inbound message validation · `S`

- [ ] Done

**Evidence:** `app/api/v1/websocket/chat.py:62-87` parses client JSON (`json.loads(raw)`) and dispatches on `type` with no schema validation; `matches.py:40` similar. `security_plan` Section 8.2 (unchecked). Malformed input → unhandled exception → the WS loop breaks silently (`except Exception: break`).

**Fix (step-by-step):**
1. Define Pydantic models for each inbound WS message (`ping`, `typing`, `typing_stopped`, `read`).
2. Validate before dispatch:
   ```python
   try:
       data = json.loads(raw)
       msg = WsInbound.model_validate(data)
   except (json.JSONDecodeError, ValidationError):
       await websocket.send_text(json.dumps({"type":"error","reason":"bad_message"}))
       continue
   ```
3. Cap array sizes: on `read`, reject `message_ids` longer than ~200 to prevent a giant-payload DoS.

**Files to touch:** `app/schemas/message.py` (add WS inbound models), `app/api/v1/websocket/chat.py`, `app/api/v1/websocket/matches.py`.
**Commands:** `pytest tests/done/ -k websocket -v`.
**Verify:** Send `{"type":"read","message_ids":[200 UUIDs]}` → handled; send garbage JSON → `error` frame, connection stays open.
**Gotchas:** Don't echo the raw payload in the error (could leak data). Keep `except Exception: break` as a last resort but log the reason first so GlitchTip sees it.

### P3-2 — HSTS + full security headers (only meaningful after P0-5) · `XS`

- [ ] Done

**Evidence:** `nginx.conf:44-46` has X-Frame-Options/X-Content-Type-Options/X-XSS-Protection but no HSTS (only inside the commented SSL block).
**Fix:** Add `add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;` in the 443 block once TLS (P0-5) is live. Consider a `Content-Security-Policy` for the Swagger UI if you ever expose it.
**Files to touch:** `nginx/nginx.conf`.
**Commands:** `docker compose restart nginx`.
**Verify:** `curl -sI https://api.<domain>/health | grep -i strict-transport`.
**Gotchas:** HSTS is permanent-ish for users — only enable after TLS is stable. Start with a short `max-age` (e.g. 300) for a day, then raise.

### P3-3 — Refresh-token theft detection (family tracking) · `M`

- [ ] Done

**Evidence:** `security_plan` lists "Token theft detection ❌". `redis.py` stores opaque refresh tokens but no family/rotation tracking — a stolen refresh token works until 30-day expiry.
**Fix (step-by-step):**
1. On each `/auth/refresh`, rotate the token and record the old jti as "rotated-from" within a family id (store `family:{family_id}` → set of jtis, plus `jti:{jti}` → family_id + status).
2. If a "rotated-from" jti is ever presented again → it's reuse → revoke the **whole family** (delete all its jtis), forcing re-login everywhere. This is the OAuth2 refresh-rotation theft-detection pattern.
3. Pair with P1-5's `user_tokens` set for bulk revoke.
**Files to touch:** `app/core/redis.py`, `app/api/v1/endpoints/auth.py`.
**Commands:** `pytest tests/done/test_auth.py -v` (add: reuse of rotated token → whole family revoked).
**Verify:** Replay an already-rotated refresh token → 401 + all that family's tokens gone.
**Gotchas:** This is the gold-standard defense; optional before launch but expected for a dating app handling sensitive DMs.

### P3-4 — Verify admin audit-log coverage on every admin mutation · `S`

- [ ] Done

**Evidence:** `admin_log` table + `log_admin_action()` exist (Session 42). Need to confirm **every** admin write endpoint calls it.
**Fix (step-by-step):**
1. `grep -L log_admin_action app/api/v1/endpoints/admin_*.py` to find admin mutation endpoints missing the call.
2. Add `await log_admin_action(...)` to each missing one (actor, action, target, timestamp).
3. Add tests that each admin write produces an `admin_logs` row.
**Files to touch:** any `admin_*.py` endpoint missing the call; tests.
**Commands:** `pytest tests/done/ -k admin -v`.
**Verify:** Grep returns zero admin mutation endpoints without `log_admin_action`.
**Gotchas:** Read-only admin endpoints (dashboard stats) don't strictly need logging, but writes (ban, photo approve, message delete) absolutely do.

## 🔵 P4 — Observability & operations

### P4-1 — GlitchTip/Sentry DSN must point at a reachable URL · `XS` · NEW

- [ ] Done

**Evidence:** `.env:119` `GLITCHTIP_DSN=http://...@localhost:8080/1`. In prod the app container can't reach `localhost:8080` (that's the host). `main.py:55` only inits Sentry if `GLITCHTIP_DSN` is set — so errors silently aren't captured.
**Fix (step-by-step):**
1. Set the DSN to the container-reachable GlitchTip: `http://<publickey>@glitchtip:80/1` (or the public GlitchTip URL).
2. Confirm `traces_sample_rate=0.1` in `main.py:65` is sensible (it is).
3. Test capture from inside the container.
**Files to touch:** `.env` (prod), `main.py`.
**Commands:** `docker exec dating_app python -c "import sentry_sdk; sentry_sdk.capture_message('test'); sentry_sdk.flush()"`.
**Verify:** The test message appears in the GlitchTip UI.
**Gotchas:** If GlitchTip is down, the Sentry SDK fails open (no crash) — good. Make sure the DSN's project number matches the GlitchTip project you created.

### P4-2 — Add app metrics (latency, DB pool, Redis, queue depth) · `M` · NEW

- [ ] Done

**Evidence:** No metrics endpoint exists — only `/health` and `/health/ready`. structlog gives logs, not time-series metrics. You can't see RPS, p99 latency, or DB pool saturation.
**Fix (step-by-step):**
1. Add `prometheus-fastapi-instrumentator` and expose `/metrics`:
   ```python
   from prometheus_fastapi_instrumentator import Instrumentator
   Instrumentator().instrument(app).expose(app, endpoint="/metrics")
   ```
2. Add custom gauges: DB pool checked-out (`engine.pool.checkedout()`), Redis command latency, WS connection count (`sum(len(s) for s in websocket_manager.active_connections.values())`), Celery queue depth (if P1-7).
3. Stand up Grafana pointed at a Prometheus scraping `/metrics`.
**Files to touch:** `app/main.py`, `requirements.txt`.
**Commands:** `curl localhost:8000/metrics`.
**Verify:** `/metrics` returns Prometheus text; Grafana shows RPS + p99 latency.
**Gotchas:** Gate `/metrics` behind the internal network (don't expose it publicly) — it can leak cardinality/internal state. Use `include_in_schema=False` so it doesn't show in dev Swagger.

### P4-3 — Backup strategy for Postgres + MinIO · `M` · NEW

- [ ] Done

**Evidence:** No backup config anywhere. `postgres_data` and `minio_data` are named volumes with no schedule. A host/disk failure = total data loss (all users, messages, photos).
**Fix (step-by-step):**
1. Postgres: nightly `pg_dump` to off-box (S3/B2) cron, or WAL-G for PITR:
   ```sh
   docker exec dating_db pg_dump -U dating_user dating_db | gzip | \
     aws s3 cp - s3://bondi-backups/$(date +%F).sql.gz
   ```
2. MinIO: `mc mirror` cron to a remote MinIO/S3, or enable bucket versioning + replication.
3. Document a **restore test** runbook (`docs/restore_test.md`) and actually run it quarterly.
**Files to touch:** new `scripts/backup.sh`, `scripts/restore_test.sh`, cron or a docker sidecar.
**Commands:** `bash scripts/backup.sh && bash scripts/restore_test.sh`.
**Verify:** Restore the dump into a fresh DB → app starts, data intact.
**Gotchas:** Encrypted messages (P0-8) mean backups contain ciphertext — fine, but the `ENCRYPTION_SECRET` must be backed up **separately** and securely (lost secret = lost chats). Test restores regularly; an untested backup is not a backup.

### P4-4 — `alembic/env.py` add `compare_type=True` · `XS` · NEW

- [ ] Done

**Evidence:** `alembic/env.py:41-45` configures `context.configure(...)` without `compare_type=True` → autogenerate won't detect column type changes (e.g. `String(20)` → `String(50)`), so migrations silently miss them.
**Fix:**
```python
context.configure(connection=conn, target_metadata=target_metadata,
                  include_object=include_object, compare_type=True,
                  compare_server_default=True)
```
**Files to touch:** `alembic/env.py`.
**Commands:** `alembic revision --autogenerate -m "test" && alembic downgrade -1`.
**Verify:** Change a column type in a model → autogenerate detects it.
**Gotchas:** `compare_server_default=True` can be noisy on `server_default=func.now()` across PG versions — review generated diffs carefully.

### P4-5 — Tests create tables from models, not migrations · `S` · NEW

- [ ] Done

**Evidence:** `tests/conftest.py` builds the schema from `Base.metadata` (drop/create), so a missing/bad migration would never fail CI. Combined with P0-1 (migrations not in git), the shipped schema is effectively untested.
**Fix (step-by-step):**
1. Once P0-1 commits the migrations, add a CI job that runs `alembic upgrade head` on a fresh DB and asserts `alembic check` (no drift) passes.
2. Optionally add a test fixture that runs migrations instead of `create_all` for one suite.
**Files to touch:** `.github/workflows/deploy.yml`, maybe `tests/conftest.py`.
**Commands:** `alembic upgrade head && alembic check`.
**Verify:** CI fails if a model change isn't reflected in a migration.
**Gotchas:** Keep the fast `Base.metadata` tests for speed; add the migration-apply job as a separate, slower stage.

## 🟣 P5 — Flutter app (from performance_plan Phase 5 + dev.md)

> The Flutter source isn't in this repo, but these are the remaining unchecked items
> from `performance_plan.md:958-966` and `dev.md:1632-1639`. Tracked here so the master
> file is complete. Effort assumes the Flutter app lives in a sibling repo.

### P5-1 — Add `dio_cache_interceptor` + Hive store · `S`
- [ ] Done
**What:** Add `dio_cache_interceptor` with a Hive store to `api_service.dart` so static GETs (interests, locations, plans, system status) are cached on-device.
**Verify:** Airplane mode → open app → interests/locations still render.

### P5-2 — Set per-endpoint cache policies · `XS`
- [ ] Done
**What:** `GET /discover` and `/GET /search` must be `NoCache` (results change every swipe/location). Static endpoints = `Cache`.
**Gotchas:** Getting this wrong = stale swipe deck.

### P5-3 — Configure `CachedNetworkImage` with size limits · `XS`
- [ ] Done
**What:** Use `CachedNetworkImage` everywhere with `memCacheWidth`/`memCacheHeight` to avoid decoding full 5000px images into memory.

### P5-4 — Add `shimmer` package + `_ShimmerAvatar` placeholder · `XS`
- [ ] Done
**What:** Replace blank circles with shimmer placeholders while photos load.

### P5-5 — Replace `Consumer` with `Selector` in hot-rebuild paths · `S`
- [ ] Done
**What:** Profile/chat-list screens: use `Selector<AuthProvider, String?>` so a name change doesn't rebuild the whole screen.
**Verify:** Flutter DevTools "Rebuild Statistics" — only the changed widget rebuilds.

### P5-6 — Audit all list screens for `ListView.builder` + `RepaintBoundary` · `XS`
- [ ] Done
**What:** Matches, search, notifications, blocks lists must be lazy `ListView.builder` with each item wrapped in `RepaintBoundary(key: ValueKey(item.id))`.

### P5-7 — Parallelize splash screen calls with `Future.wait` · `XS`
- [ ] Done
**What:** `Future.wait([systemService.getStatus(), checkVersion(), storage.loadTokens()])` instead of sequential awaits.

### P5-8 — Add WebSocket exponential backoff reconnection · `S`
- [ ] Done
**What:** On WS disconnect, retry with `delay = min(30, 1 << retryCount)` seconds, cap 6 retries (~60s). Reset on success.
**Gotchas:** Stop retrying on explicit logout (auth token invalid).

### P5-9 — Add pagination to notifications screen · `XS`
- [ ] Done
**What:** Load 20 at a time, append on scroll (`offset`), stop when `next_offset` is null.

---

## ⚫ P6 — Deployment checklist (from dev.md:2085-2091)

- [ ] P6-1 Purchase VPS (Hetzner CX22 / DigitalOcean droplet — 2 vCPU, 4GB RAM min)
- [ ] P6-2 Install Docker + Docker Compose on VPS
- [ ] P6-3 Clone repo to `/opt/dating-app`
- [ ] P6-4 Configure `.env` on server (copy `.env.example`, set **real** production secrets — see P0-8)
- [ ] P6-5 Run initial deploy: `bash scripts/deploy.sh`
- [ ] P6-6 Set up firewall: `sudo bash scripts/firewall.sh`
- [ ] P6-7 Add GitHub Secrets for auto-deploy (`VPS_HOST`, `VPS_USERNAME`, `VPS_PASSWORD`, `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`)

> The CI pipeline is `.github/workflows/deploy.yml` — it runs `pytest tests/done/` then SSH-deploys via `scripts/deploy.sh`. Note: CI installs deps with `pip install -r requirements.txt` (no lockfile) and runs tests against ephemeral GitHub services; it does **not** run migrations (it builds tables from models) — see P4-5.

---

## Suggested implementation order

| # | Task | Effort | Why first |
|---|------|--------|-----------|
| 1 | **P0-1** migrations: stop autogenerate + commit to git + init container | XS | ✅ done (`b3d76b2`) |
| 2 | **P0-2** cache `get_current_user` in Redis | S | ✅ done |
| 3 | **P0-3** cache derive_chat_key + offload decrypt to threadpool | S | ✅ done |
| 4 | **P0-4** PgBouncer + pool tuning | S | Unblocks 100+ concurrent users |
| 5 | **P0-5** HTTPS | S | Required for Play Store |
| 6 | **P0-8** rotate secrets | XS | Baseline; do before launch |
| 7 | **P0-6** real ZarinPal | M | Required to monetize |
| 8 | **P0-7** real email sending | M | Required to register/reset in prod |
| 9 | **P1-1 + P1-7** FCM in executor + decide on Celery | S–M | Reliability of notifications |
| 10 | **P2-1/P2-2** Tehran TZ + naive datetimes | XS | Correctness of daily limits |
| 11 | **P1-3/P1-4** Redis pass + network isolation | S | Baseline infra security |
| 12 | **P2-7** NSFW ML model | L | Fairness + safety before public exposure |
| 13 | **P4-2/P4-3** metrics + backups | M | Operate with confidence |

> Rule of thumb: ship P0-1 through P0-5 before any public launch. P1 items before you
> actively market/grow. P2+ are hardening — knock out the `XS` ones in a single batch.

## Appendix — Stale checklist reconciliation (do NOT re-do these)

The following items are **unchecked in the old docs but already implemented** in code.
Listed so you don't waste a session re-doing them. Delete the stale `[ ]` boxes in the
source docs (`scale_plan.md`, `security_plan.md`, `performance_plan.md`) when you next
touch them.

- ✅ WebSocket rewrite / Redis Pub/Sub multi-worker — `websocket_manager.py` (scale §3.4, Session B)
- ✅ Presence (who's online) — `websocket_manager.py:246-261`
- ✅ Typing indicators — `websocket_manager.py:263-292`
- ✅ `validate_ws_token` in deps — `deps.py:154-167`
- ✅ `is_online` in match/search/discover responses — `matches.py`, `discover.py:237`, `search.py:324`
- ✅ Discover card stack pre-caching — `cache.py:81-122`
- ✅ Swipe deduplication (Redis set) — `cache.py:126-143`
- ✅ All `broadcast_match`/`send_to_match` callers pass `redis` — `swipes.py:79`, `messages.py:77`
- ✅ structlog JSON logging — `core/logging.py` (scale §7, Session D)
- ✅ Sentry/GlitchTip SDK — `main.py:55-67` (scale §7, Session D) — but see P4-1 (DSN)
- ✅ FCM push + device-token endpoint — `push_service.py`, `notifications.py:103` (scale §9, Session E) — but see P1-1
- ✅ Docker health checks + dynamic multi-worker — `entrypoint.sh`, `compose` (scale §8)
- ✅ Nginx reverse proxy (HTTP) — `nginx.conf`
- ✅ Security headers (X-Frame/X-CTO/X-XSS) — `nginx.conf:44-46`
- ✅ Auth hardening (15-min token, OTP brute-force, enumeration, timing) — `security.py`, `redis.py`
- ✅ Location fuzzing ±500m — `utils/geo.py`
- ✅ Per-match message rate limit (limiter present) — verify keying P1-6
- ✅ Daily report limit — `reports.py`
- ✅ Registration IP logging + same-IP detection — Session 42
- ✅ Admin JWT tokens + `admin_logs` table + `log_admin_action` — Session 42 (verify coverage P3-4)
- ✅ CORS configurable — `main.py:84-91`
- ✅ Swagger/Redoc disabled in prod — `main.py:73-75`
- ✅ EXIF stripping + PIL validation — `photo_service.py:87-95`
- ✅ IDOR ownership checks on messages/photos/notifications/tickets — done in services
- ✅ DB indexes (37 across 10 tables), Redis caching, Haversine in PG, N+1 fixes, cursor pagination, GZip — all done

---

## Cross-reference index (which old-doc items map to which task)

| Old-doc source | Old unchecked item | New task ID |
|----------------|--------------------|-------------|
| scale §1 Session A | PgBouncer | P0-4 |
| scale §1 | prepared_statement_cache_size=0 | P0-4 |
| scale §2 Session A | rate limits on auth/discover/search | ✅ done (verify P1-6) |
| scale §3 Session B | WS rewrite, presence, typing | ✅ done |
| scale §4 Session B | discover card stack cache | ✅ done |
| scale §5 Session B | swipe dedup set | ✅ done |
| scale §6 Session C | nginx + SSL | P0-5 |
| scale §8 Session C | health checks + workers | ✅ done |
| scale §7 Session D | structlog + Sentry | ✅ done (DSN P4-1) |
| scale §9 Session E | FCM push + device-token | ✅ done (P1-1) |
| scale §10 Session F | real ZarinPal | P0-6 |
| security §6.1 | real SECRET_KEY/ENCRYPTION_SECRET | P0-8 |
| security §6.2 | .gitignore covers secrets | ✅ done |
| security §8.1/8.2 | WS token + message validation | P3-1 (token ✅, message open) |
| security §9.1/9.2/9.3 | network isolation / Redis pass / MinIO creds | P1-3, P1-4 |
| security §5.2 | nginx security headers | ✅ done (+ HSTS P3-2) |
| perf §5 (Phase 5) | Flutter items | P5-1 … P5-9 |
| dev.md §13 | VPS/deploy checklist | P6-1 … P6-7 |

---

*Generated 2026-08-02 from a full backend codebase audit. Items tagged `NEW` were not
present in any prior doc. Update `[ ]` → `[x]` as you ship; add new findings at the
bottom of the relevant section.*

















