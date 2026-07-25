# Security Plan
## Iranian Dating App — Backend + Flutter

> Based on your actual codebase (dev.md, Sessions 1–31).
> Every issue listed here is specific to your architecture.
> Work through sections top to bottom. Each section is one session.

---

## Current Security Inventory (What You Already Have)

| Control | Status | Notes |
|---------|--------|-------|
| JWT access tokens | ✅ | 15-min expiry, `ver` field for invalidation |
| Opaque refresh tokens in Redis | ✅ | 30-day TTL, rotation on use |
| Token version (`token_version`) | ✅ | Password change/reset increments → old tokens rejected |
| AES-256-GCM message encryption | ✅ | Per-match key, PBKDF2 derivation |
| Bcrypt password hashing | ✅ (assumed) | `password_hash` column exists |
| MinIO private/public bucket split | ✅ | Pending photos behind signed URLs |
| Google OAuth | ✅ | |
| Rate limiting (`limiter.py`) | ✅ | Redis-backed, per-endpoint + per-match message rate + daily report limit |
| Admin key (`X-Admin-Key` header) | ✅ | JWT tokens supported, legacy key still works |
| GZip middleware | ✅ | Not security, but noted |
| Input validation (Pydantic) | ✅ | All schemas use Pydantic |
| SQLAlchemy ORM (no raw SQL) | ✅ | No SQL injection risk |
| HTTPS / TLS | ❌ | Nginx + SSL not yet configured |
| Secrets in `.env` | ⚠️ | `SECRET_KEY=your-secret-key` in example — change in prod |
| CORS policy | ✅ | Configurable via CORS_ORIGINS env var |
| Security headers | ❌ | No X-Frame-Options, CSP, HSTS, etc. |
| File upload validation | ✅ | Size, dimension, EXIF stripping, PIL format validation |
| Photo content scanning | ✅ | NSFW detection (skin-tone heuristic, threshold 0.8) — ML-based upgrade (opennsfw2/TensorFlow) planned for production |
| Account enumeration protection | ✅ | register/init returns same response for all emails |
| OTP brute-force protection | ✅ | 5 max attempts per code (register + password reset) |
| Timing attack protection | ✅ | Login returns uniform "Incorrect email or password" |
| Swagger/Redoc in production | ✅ | Disabled when ENVIRONMENT != "development" |
| Audit log | ❌ | No record of admin actions |
| Token theft detection | ❌ | No refresh token family tracking |

---

## Section 1 — Authentication Hardening

### 1.1 — Access Token Expiry is Too Long

**Current:** `ACCESS_TOKEN_EXPIRE_MINUTES=10080` = **7 days**.

A stolen JWT is valid for 7 days with zero ability to revoke it (short of changing
`SECRET_KEY` and logging out every user in the app).

**Fix:** Shorten to 15 minutes. The refresh token (Redis, 30 days) handles silent renewal.

```env
# .env
ACCESS_TOKEN_EXPIRE_MINUTES=15
```

```python
# Flutter: api_service.dart — already has Dio interceptor for 401 handling
# When a request returns 401, interceptor calls /auth/refresh automatically
# This is standard practice — 15min access + 30day refresh is the industry norm
```

The `token_version` field you already have handles force-logout (password change,
account compromise). With 15-min tokens, the window is now 15 minutes, not 7 days.

### 1.2 — Account Enumeration via Register/Init

**Current:** `POST /auth/register/init` checks if email exists and likely returns
different responses for "email already registered" vs "code sent".

**Problem:** An attacker can enumerate which emails are registered by calling this
endpoint in a loop:
- Response A = "code sent" → email NOT registered
- Response B = "already exists" → email IS registered

This is a privacy violation, especially sensitive for a dating app where users
may not want their participation known.

**Fix:** Return the **same response and same HTTP status** regardless:

```python
# endpoints/auth.py
@router.post("/register/init", status_code=200)
async def register_init(payload: RegisterInitRequest, background_tasks: BackgroundTasks, ...):
    user = await get_user_by_email(payload.email, db)

    if user and user.registration_status == "onboarding_complete":
        # Email exists — send a "login instead" email in background, but return SAME response
        background_tasks.add_task(
            email_service.send_already_registered_notice, payload.email
        )
    else:
        # New email — send verification code
        background_tasks.add_task(
            send_verification_code, payload.email, redis
        )

    # ALWAYS return the same message — attacker learns nothing
    return {"message": "If this email is new, a verification code has been sent."}
```

### 1.3 — Login Timing Attack

**Current:** `POST /auth/login` likely returns:
- "User not found" for unknown email
- "Wrong password" for known email + wrong password

These responses (and their response times) allow attackers to confirm which emails
are registered.

**Fix 1 — Uniform error message:**
```python
# Always return the same error regardless of which check failed
raise HTTPException(status_code=401, detail="Incorrect email or password.")
```

**Fix 2 — Constant-time comparison for passwords:**
```python
# Even if the user is not found, still run bcrypt to normalize response time
# Without this, "user not found" returns in 1ms, "wrong password" returns in 100ms
# The timing difference alone reveals which emails are registered

async def verify_login(email: str, password: str, db: AsyncSession) -> User:
    user = await get_user_by_email(email, db)

    if user is None:
        # Run a dummy bcrypt hash to normalize timing — then still fail
        dummy_hash = "$2b$12$eImiTXuWVxfM37uY4JANjQrNemN8oNzMW5Yj6L8wbhGZjkMKjuYS"
        bcrypt.checkpw(password.encode(), dummy_hash.encode())
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    return user
```

### 1.4 — OTP Brute-Force Protection

**Current:** Verification code is 6 digits (Redis, 5-min TTL).
6 digits = 1,000,000 combinations. With no attempt limit, an attacker can brute-force
the code in under 5 minutes by trying all combinations.

**Fix:** Store attempt counter alongside the code in Redis:

```python
# core/redis.py — updated verification functions

async def store_verification_code(email: str, code: str, redis: Redis):
    key = f"verification:{email}"
    await redis.setex(key, 300, json.dumps({
        "code": code,
        "attempts": 0,
        "created_at": time.time()
    }))

async def verify_code(email: str, submitted_code: str, redis: Redis) -> bool:
    key = f"verification:{email}"
    raw = await redis.get(key)
    if not raw:
        raise HTTPException(400, "Code expired or not found.")

    data = json.loads(raw)

    if data["attempts"] >= 5:
        await redis.delete(key)   # invalidate — must request new code
        raise HTTPException(429, "Too many attempts. Request a new code.")

    if data["code"] != submitted_code:
        data["attempts"] += 1
        ttl = await redis.ttl(key)
        await redis.setex(key, ttl, json.dumps(data))
        raise HTTPException(400, f"Invalid code. {5 - data['attempts']} attempts left.")

    await redis.delete(key)   # single-use
    return True
```

### 1.5 — Password Reset Code Same Issue

Same brute-force risk applies to `POST /auth/password-reset/verify`.
Apply the same attempt counter pattern from 1.4 to the password reset code.

Also add: after a successful password reset, increment `token_version` to invalidate
all existing tokens (your architecture already supports this).

```python
# endpoints/auth.py — after successful password reset
user.token_version += 1
await db.commit()
# This invalidates all existing JWTs for this user
```

---

## Section 2 — Admin Panel Security

### 2.1 — Admin Key is a Static String (Critical)

**Current:** Admin endpoints are protected by `X-Admin-Key: your-admin-key` header.

**Problems:**
- Static string — if it leaks (logs, network sniff, `.env` file), it never expires
- No per-admin identity — you can't tell which admin did what
- No way to revoke one admin's access without changing the key for everyone
- The key is in `.env` in plaintext

**Fix:** Replace with short-lived admin JWT tokens:

```python
# core/security.py — add admin token functions

ADMIN_TOKEN_EXPIRE_MINUTES = 60   # 1-hour admin sessions

def create_admin_token(admin_id: str) -> str:
    payload = {
        "sub": admin_id,
        "role": "admin",
        "exp": datetime.utcnow() + timedelta(minutes=ADMIN_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.ADMIN_SECRET_KEY, algorithm="HS256")

def verify_admin_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.ADMIN_SECRET_KEY, algorithms=["HS256"])
        if payload.get("role") != "admin":
            raise credentials_exception
        return payload
    except JWTError:
        raise HTTPException(status_code=403, detail="Invalid or expired admin token.")
```

```python
# New endpoint: POST /api/v1/admin/login
@router.post("/admin/login")
async def admin_login(
    payload: AdminLoginRequest,   # {username, password}
):
    # Verify against ADMIN_USERNAME + ADMIN_PASSWORD in .env (hashed)
    if not verify_admin_credentials(payload.username, payload.password):
        raise HTTPException(403, "Invalid credentials.")
    token = create_admin_token(admin_id=payload.username)
    return {"access_token": token, "expires_in": 3600}
```

```python
# core/deps.py — replace X-Admin-Key header check
async def get_current_admin(
    authorization: str = Header(None),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(403, "Admin token required.")
    token = authorization.split(" ")[1]
    return verify_admin_token(token)
```

**Add to `.env`:**
```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$2b$12$...   # bcrypt hash of your admin password
```

### 2.2 — Admin Action Audit Log

**Current:** No record of what admins do. If someone abuses the admin panel
(approves fake photos, deletes reports, views private messages), there is no trace.

**Fix:** New table + middleware:

```python
# models/admin_log.py
class AdminLog(Base):
    __tablename__ = "admin_logs"
    id = Column(UUID, primary_key=True, default=uuid4)
    admin_id = Column(String(100), nullable=False)   # admin username
    action = Column(String(100), nullable=False)      # "approve_photo", "delete_message", etc.
    target_type = Column(String(50))                  # "photo", "message", "user"
    target_id = Column(UUID)                          # the affected resource
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_admin_logs_admin", "admin_id", "created_at"),
        Index("idx_admin_logs_target", "target_type", "target_id"),
    )
```

```python
# Usage in admin endpoints:
async def log_admin_action(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: UUID,
    request: Request,
    db: AsyncSession,
):
    log = AdminLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=request.client.host,
    )
    db.add(log)
    await db.commit()

# In admin_photos.py:
await log_admin_action(admin.sub, "approve_photo", "photo", photo_id, request, db)

# In admin_messages.py:
await log_admin_action(admin.sub, "decrypt_message", "message", message_id, request, db)
```

---

## Section 3 — Authorization (IDOR Protection)

IDOR = Insecure Direct Object Reference. User A accesses User B's resource by
guessing/changing the ID in the URL.

### 3.1 — Messages: Verify Match Membership

**Risk:** `GET /api/v1/messages/{identifier}` — does it verify that the requesting
user is actually a participant in the match?

**Required check in messages.py:**
```python
async def get_match_or_403(
    identifier: str,
    current_user_id: UUID,
    db: AsyncSession
) -> Match:
    match = await db.get(Match, identifier)
    if not match:
        raise HTTPException(404, "Match not found.")

    # CRITICAL: verify the requesting user is IN this match
    if match.user1_id != current_user_id and match.user2_id != current_user_id:
        raise HTTPException(403, "Access denied.")   # NOT 404 — 404 would confirm it exists

    return match
```

### 3.2 — Photos: Verify Ownership

**Risk:** `DELETE /api/v1/users/me/photos/{photo_id}` and
`PUT /api/v1/users/me/photos/{photo_id}/main` — do they verify the photo
belongs to the requesting user?

```python
# In photos.py — every photo mutation endpoint
async def get_own_photo_or_403(
    photo_id: UUID,
    current_user_id: UUID,
    db: AsyncSession
) -> Photo:
    photo = await db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found.")
    if photo.user_id != current_user_id:
        raise HTTPException(403, "Access denied.")
    return photo
```

### 3.3 — Notifications: Verify Ownership

**Risk:** `DELETE /api/v1/notifications/{notification_id}` and
`POST /api/v1/notifications/read` — can user A delete user B's notifications?

```python
# notifications.py
notification = await db.get(Notification, notification_id)
if notification.user_id != current_user_id:
    raise HTTPException(403, "Access denied.")
```

### 3.4 — Tickets: Verify Ownership

**Risk:** `GET /api/v1/tickets/{ticket_id}` — can user A read user B's support ticket?

```python
ticket = await db.get(Ticket, ticket_id)
if ticket.user_id != current_user_id:
    raise HTTPException(403, "Access denied.")
```

### 3.5 — Messages: Delete Authorization

**Risk:** `DELETE /api/v1/messages/{message_id}` — only sender or receiver should
be able to soft-delete a message. Not any authenticated user.

```python
message = await db.get(Message, message_id)
if message.sender_id != current_user_id and message.receiver_id != current_user_id:
    raise HTTPException(403, "Access denied.")
```

---

## Section 4 — File Upload Security

### 4.1 — MIME Type Validation (Not Just Extension)

**Current:** `ALLOWED_CHAT_IMAGE_FORMATS=JPEG,PNG,WEBP,JPG` exists in config.
But checking the file *extension* is not enough — an attacker can rename `evil.php`
to `evil.jpg` and upload it.

**Fix:** Read the actual file magic bytes (first few bytes of the file):

```python
# services/photo_service.py — add MIME validation

import magic   # pip install python-magic

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

def validate_image_mime(file_bytes: bytes) -> str:
    """Returns MIME type or raises HTTPException."""
    mime = magic.from_buffer(file_bytes[:2048], mime=True)
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: {mime}. Upload JPEG, PNG, or WebP."
        )
    return mime
```

```bash
# requirements.txt
python-magic==0.4.27
# Also needs system lib:
# sudo apt install libmagic1
```

### 4.2 — Image Dimension / Resolution Limits

**Current:** Size limit in MB exists. No dimension check.

An attacker can upload a 9MB PNG that is 50,000×50,000 pixels — a "decompression
bomb". When any code tries to open it with Pillow for resizing/thumbnail, it will
consume gigabytes of RAM and crash the process.

**Fix:**
```python
from PIL import Image
import io

MAX_DIMENSION = 8000   # pixels on any side

def validate_image_dimensions(file_bytes: bytes):
    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            w, h = img.size
            if w > MAX_DIMENSION or h > MAX_DIMENSION:
                raise HTTPException(400, f"Image too large: {w}×{h}. Max {MAX_DIMENSION}px per side.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(400, "Invalid image file.")
```

### 4.3 — Strip EXIF Data Before Storing

**Current:** Photos stored as-is in MinIO.

**Problem:** EXIF data in JPEG files contains GPS coordinates, device model, and
timestamp. A user who uploads a photo taken at home exposes their home location
in the file metadata — even if they set their profile location to a different city.

**Fix:** Strip EXIF on upload before storing to MinIO:

```python
from PIL import Image
import io

def strip_exif(file_bytes: bytes, mime_type: str) -> bytes:
    """Strip all EXIF metadata from image. Returns clean bytes."""
    if mime_type not in ("image/jpeg", "image/png", "image/webp"):
        return file_bytes

    with Image.open(io.BytesIO(file_bytes)) as img:
        # Convert to RGB to drop any metadata channel
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))

        output = io.BytesIO()
        save_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime_type]
        clean.save(output, format=save_format, quality=90)
        return output.getvalue()
```

Add `Pillow` to requirements if not already there.

---

## Section 5 — CORS and Security Headers

### 5.1 — CORS Policy

**Current:** Unknown. FastAPI's default CORS is wide open if not explicitly configured.
If `CORSMiddleware` is in `main.py`, what origins are allowed?

**Correct production CORS for a mobile app backend:**
```python
# main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://yourapp.ir",       # web app domain if any
        "https://api.yourapp.ir",   # self (for Swagger UI)
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)

# For pure mobile app with NO web frontend:
# allow_origins=[] and allow_credentials=False is fine
# Mobile apps don't use CORS — it's a browser mechanism only
```

**Note for Flutter:** CORS does not apply to Flutter mobile apps. It only affects
browser-based clients. If you have no web frontend, CORS configuration only matters
for your Swagger/Redoc docs access.

### 5.2 — Security Headers via Nginx

Already covered in `scale_plan.md` (Section 6 — Nginx config). Add these headers
to the Nginx `server {}` block:

```nginx
# Prevent clickjacking
add_header X-Frame-Options "DENY" always;

# Prevent MIME sniffing
add_header X-Content-Type-Options "nosniff" always;

# XSS protection (legacy browsers)
add_header X-XSS-Protection "1; mode=block" always;

# HTTPS only for 1 year
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

# Referrer policy
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

# Content Security Policy (for Swagger/Redoc UI only)
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';" always;

# Don't expose server version
server_tokens off;
```

### 5.3 — Hide FastAPI Version in Error Responses

**Current:** FastAPI by default includes `"detail"` fields that may leak framework info.

```python
# main.py — disable default server header exposure
app = FastAPI(
    title="Dating App API",
    docs_url="/api/docs" if settings.DEBUG else None,      # disable Swagger in prod
    redoc_url="/api/redoc" if settings.DEBUG else None,    # disable Redoc in prod
    openapi_url="/api/openapi.json" if settings.DEBUG else None,
)
```

Disabling Swagger/Redoc in production means attackers can't easily enumerate
your endpoints or test inputs through the UI.

---

## Section 6 — Secrets Management

### 6.1 — Secrets Audit

From your `.env.example`:

| Variable | Risk | Action |
|----------|------|--------|
| `SECRET_KEY=your-secret-key` | 🔴 Placeholder in example | Must be 64+ random bytes in production |
| `ADMIN_SECRET_KEY=your-admin-key` | 🔴 Placeholder | Same |
| `ENCRYPTION_SECRET=your-super-secret-32-byte-key-here-change-in-production` | 🔴 Hint in value | Change before first real user |
| `S3_ACCESS_KEY=minioadmin` | 🟡 Default credential | Change MinIO creds in production |
| `S3_SECRET_KEY=minioadmin` | 🟡 Default credential | Change MinIO creds in production |

**Generate strong secrets:**
```bash
# SECRET_KEY (64 bytes):
python3 -c "import secrets; print(secrets.token_hex(64))"

# ENCRYPTION_SECRET (exactly 32 bytes for AES-256):
python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])"
```

### 6.2 — Never Commit `.env` to Git

Verify `.gitignore` has:
```gitignore
.env
.env.test
*.env
firebase-service-account.json
maintenance.json
version_override.json
```

Check current git history hasn't already committed secrets:
```bash
git log --all --full-history -- .env
# If any commits show up, the secret is in git history even if you delete the file
# In that case: rotate ALL secrets immediately (new SECRET_KEY, ENCRYPTION_SECRET, etc.)
```

### 6.3 — ENCRYPTION_SECRET Rotation Plan

If `ENCRYPTION_SECRET` is ever compromised, all message content in the DB is at risk.
Document the rotation procedure now while the app is empty:

```
Rotation procedure:
1. Deploy new ENCRYPTION_SECRET to .env
2. Write migration script:
   - For each message: decrypt with OLD key → re-encrypt with NEW key
   - Run in transaction with rollback on error
3. Deploy migration
4. Verify messages still decrypt correctly
5. Remove OLD key

Note: This must be done in a maintenance window with 0 active users
      (messages being written during rotation would use the wrong key).
```

---

## Section 7 — Dating App Specific Threats

These are threats specific to dating apps that general security guides don't cover.

### 7.1 — Location Fuzzing (Privacy)

**Current:** `lat` and `lng` stored as exact DOUBLE precision. The discover endpoint
likely returns exact distance in km.

**Risk:** A stalker can triangulate a user's exact location by taking multiple
distance readings from different positions. With 3+ readings, they can pinpoint
the user to within a few meters.

**Fix:** Add random noise (±500m) to coordinates before using them in distance
calculations in the API response:

```python
# utils/geo.py
import random
import math

def fuzz_location(lat: float, lng: float, radius_meters: int = 500) -> tuple[float, float]:
    """
    Add random noise within radius_meters to a coordinate.
    The stored coordinates remain exact — only the value used in API responses is fuzzed.
    """
    # Convert radius to degrees (approx)
    delta_lat = (radius_meters / 111320) * random.uniform(-1, 1)
    delta_lng = (radius_meters / (111320 * math.cos(math.radians(lat)))) * random.uniform(-1, 1)
    return lat + delta_lat, lng + delta_lng
```

Apply fuzzing when building the discover/search response `distance_km` value —
NOT to the stored DB values (those need to stay exact for the Haversine filter).

### 7.2 — Fake Profile / Catfishing Detection

**Current:** `is_verified` field exists. Admin can verify via `POST /admin/photos/verify-face`.

**Missing pieces:**
- No server-side face-match between profile photo and verification selfie
- No detection of stock photos (used in mass fake account creation)
- Verified status displayed to users but the verification process is manual

For MVP: the manual admin verification flow you have is acceptable.
For post-launch: integrate a face-match API (e.g. AWS Rekognition or Azure Face).

### 7.3 — Mass Fake Account Detection

Dating apps are prime targets for bot farms creating fake profiles to scam users.

**Detection signals to log per account:**
```python
# models/user.py — add these columns
registration_ip = Column(String(45))      # IP at registration time
last_login_ip = Column(String(45))
device_fingerprint = Column(String(255))  # from Flutter: device model + OS version
```

**Basic bot detection rules (flag for admin review):**
- Same IP used for 3+ registrations in 24 hours
- Account created + profile completed + 20 likes sent in < 5 minutes
- Profile photo is a URL that 404s (impossible for MinIO uploads, but useful for future)
- Bio text matches another account's bio exactly (MD5 hash comparison)

```python
# After registration, add to background task:
async def check_registration_abuse(user_id: UUID, ip: str, redis: Redis):
    key = f"reg_ip:{ip}"
    count = await redis.incr(key)
    await redis.expire(key, 86400)   # 24-hour window
    if count >= 3:
        # Flag for admin review — don't block, just log
        await notify_admin_suspicious_registration(ip, count)
```

### 7.4 — Report Abuse / False Flagging

**Current:** `POST /api/v1/reports/{user_id}` — any user can report any other user.

**Risk:** Coordinated false reports to get a legitimate user banned.

**Fix:**
- Store report count per reporter per day (already have daily_limits pattern)
- Flag reporters who have > 5 accepted reports marked as "false" by admin
- Don't auto-ban on reports — require admin review (you already have this)

```python
# Add to daily limits or a separate table
REPORTS_PER_DAY_LIMIT = 5

async def check_report_abuse(reporter_id: UUID, redis: Redis):
    key = f"reports:{reporter_id}:{date.today()}"
    count = await redis.incr(key)
    await redis.expire(key, 86400)
    if count > REPORTS_PER_DAY_LIMIT:
        raise HTTPException(429, "Report limit reached for today.")
```

### 7.5 — Message Content Rate Limiting

**Current:** Daily limit of 10 new chats per free user. But once in a chat, how many
messages per minute can a user send?

**Risk:** A user could spam thousands of messages per minute into one chat, harassing
another user or generating server load.

**Fix:** Per-match message rate limit:

```python
# In messages.py — POST /{identifier}/text
async def check_message_rate(sender_id: UUID, match_id: UUID, redis: Redis):
    key = f"msg_rate:{sender_id}:{match_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)   # 1-minute window
    if count > 30:   # max 30 messages per minute per chat
        raise HTTPException(429, "Sending too fast. Please slow down.")
```

---

## Section 8 — WebSocket Security

### 8.1 — WebSocket Authentication

**Current:** WebSocket endpoints at `/api/v1/ws/chat` and `/api/v1/ws/matches`.

**Risk:** WebSocket connections don't send HTTP headers on every message — the JWT
must be validated at connection time. If not, unauthenticated users can connect.

**Fix:** Validate token on WebSocket handshake:

```python
# api/v1/websocket/chat.py
from fastapi import WebSocket, WebSocketDisconnect, Query, HTTPException

@router.websocket("/ws/chat/{match_id}")
async def websocket_chat(
    websocket: WebSocket,
    match_id: UUID,
    token: str = Query(...),   # JWT passed as query param (WebSocket can't use headers)
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    # Validate token BEFORE accepting the connection
    try:
        user_id = await validate_ws_token(token, redis, db)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify user is a participant in this match
    match = await get_match_or_403(str(match_id), user_id, db)
    if not match:
        await websocket.close(code=4003, reason="Access denied")
        return

    await websocket.accept()
    # ... rest of handler
```

**Note:** Passing JWT as a query param means it appears in server logs. Use a
short-lived WebSocket-specific token (1-minute TTL) to minimize exposure:

```python
# New endpoint: POST /api/v1/ws/token
# Returns a one-time, 60-second token specifically for WebSocket auth
# Flutter uses this instead of the main access token for WS connections
```

### 8.2 — WebSocket Message Validation

**Risk:** WebSocket messages are raw JSON — no Pydantic validation unless explicitly added.

**Fix:** Validate every incoming WebSocket message:

```python
# In the WebSocket handler message loop:
async for raw_message in websocket.iter_text():
    try:
        data = json.loads(raw_message)
        # Validate structure
        if data.get("type") not in ("message", "typing", "ping"):
            continue   # ignore unknown types
        if "content" in data and len(data["content"]) > 5000:
            continue   # ignore oversized messages
    except json.JSONDecodeError:
        continue   # ignore malformed JSON — don't disconnect
```

---

## Section 9 — Infrastructure Security

### 9.1 — Docker Network Isolation

**Current:** All services in the same Docker network, all ports exposed to host.

**Risk:** If any service is compromised, it can reach all other services directly.
Also, exposing PostgreSQL, Redis, and MinIO ports to the host means they're
accessible from the internet if firewall isn't configured.

**Fix:** Use Docker networks to isolate:

```yaml
# docker-compose.yml
networks:
  frontend:    # nginx ↔ api
  backend:     # api ↔ db, redis, minio
  db_only:     # db isolation

services:
  nginx:
    networks: [frontend]
    ports: ["80:80", "443:443"]   # only nginx is exposed

  api:
    networks: [frontend, backend]
    # NO ports exposed — accessed only through nginx

  db:
    networks: [db_only, backend]
    # NO ports: ["5432:5432"] in production — not accessible from host

  redis:
    networks: [backend]
    # NO ports: ["6379:6379"] in production

  minio:
    networks: [backend]
    ports: ["9000:9000"]   # only if MinIO must be publicly accessible for direct photo URLs
    # Better: serve MinIO through nginx proxy too
```

### 9.2 — Redis Authentication

**Current:** Redis likely has no password (`redis://localhost:6379`).

**Risk:** If Redis port is accidentally exposed, anyone can read refresh tokens,
verification codes, daily limits, and all cached user data.

**Fix:**
```yaml
# docker-compose.yml
redis:
  command: redis-server --requirepass ${REDIS_PASSWORD}
```

```env
# .env
REDIS_PASSWORD=strong-random-password-here
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379
```

```bash
# Generate:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 9.3 — MinIO Credentials

**Current:** `S3_ACCESS_KEY=minioadmin` / `S3_SECRET_KEY=minioadmin` — default credentials.

**Fix:** Change before any real data goes in:
```env
S3_ACCESS_KEY=your-strong-access-key
S3_SECRET_KEY=your-strong-secret-key-min-8-chars
```

Update in MinIO via `docker exec` or the console before first production deploy:
```bash
docker exec dating_minio mc admin user add local newadmin newstrongpassword
docker exec dating_minio mc admin policy attach local readwrite --user newadmin
docker exec dating_minio mc admin user remove local minioadmin
```

---

## Implementation Order

### 🔴 Session A — Critical Auth Fixes (do before any real users)

- [x] Shorten `ACCESS_TOKEN_EXPIRE_MINUTES` to 15 (Section 1.1) ✅ Session 35
- [x] Add uniform error message to login (Section 1.3 — Fix 1) ✅ (was already done)
- [x] Add constant-time password check to login (Section 1.3 — Fix 2) ✅ Session 42
- [x] Add OTP attempt counter (5 attempts max) (Section 1.4) ✅ Session 35
- [x] Apply same attempt counter to password reset (Section 1.5) ✅ Session 35
- [x] Add `token_version` increment to password reset (Section 1.5) ✅ (was already done)
- [ ] Generate real `SECRET_KEY` and `ENCRYPTION_SECRET` for production (Section 6.1)
- [x] Verify `.gitignore` covers all secret files (Section 6.2) ✅ Session 41

### 🔴 Session B — IDOR Protection

- [x] Add match membership check to all `/messages/{identifier}` endpoints (Section 3.1) ✅ (already done)
- [x] Add ownership check to all photo mutation endpoints (Section 3.2) ✅ (already done)
- [x] Add ownership check to notification delete/read endpoints (Section 3.3) ✅ (already done)
- [x] Add ownership check to ticket read endpoint (Section 3.4) ✅ (already done)
- [x] Add sender/receiver check to message delete (Section 3.5) ✅ (already done)
- [x] Add MIME type validation to photo upload (Section 4.1) ✅ (PIL implicit validation)
- [x] Add image dimension check to photo upload (Section 4.2) ✅ (already done)
- [x] Add EXIF stripping to photo upload (Section 4.3) ✅ Session 36

### 🟡 Session C — Admin Security

- [x] Replace static `X-Admin-Key` with JWT admin tokens (Section 2.1) ✅ Session 42
- [x] Create `POST /admin/login` endpoint (Section 2.1) ✅ Session 42
- [x] Create `admin_logs` table + migration (Section 2.2) ✅ Session 42
- [x] Add `log_admin_action()` to all admin endpoints (Section 2.2) ✅ Session 42
- [x] Add CORS configuration to `main.py` (Section 5.1) ✅ Session 40
- [x] Disable Swagger/Redoc in production (Section 5.3) ✅ Session 35

### 🟡 Session D — Infrastructure + WebSocket Security

- [ ] Add Redis password to docker-compose + `.env` (Section 9.2)
- [ ] Change MinIO default credentials (Section 9.3)
- [ ] Add Docker network isolation to docker-compose.yml (Section 9.1)
- [ ] Add WebSocket token validation on handshake (Section 8.1)
- [ ] Add WebSocket message validation (Section 8.2)
- [ ] Add security headers to Nginx config (Section 5.2)

### 🟢 Session E — Dating App Specific

- [x] Add location fuzzing (±500m) to discover/search distance response (Section 7.1) ✅ Session 37
- [x] Add per-match message rate limit (30/min) (Section 7.5) ✅ Session 38
- [x] Add report abuse daily limit (5 reports/day) (Section 7.4) ✅ Session 39
- [x] Add registration IP logging (Section 7.3) ✅ Session 42
- [x] Add same-IP registration detection (Section 7.3) ✅ Session 42
- [x] Document `ENCRYPTION_SECRET` rotation procedure (Section 6.3) ✅ (already in security_plan.md)

### 🟢 Session F — Account Enumeration (lower priority, but clean)

- [x] Unify register/init response regardless of email existence (Section 1.2) ✅ Session 35

---

## Security Test Checklist

After each session, verify with these manual tests:

```bash
# Test timing attack fix (Section 1.3):
time curl -X POST /api/v1/auth/login -d '{"email":"nonexistent@x.com","password":"x"}'
time curl -X POST /api/v1/auth/login -d '{"email":"real@user.com","password":"wrong"}'
# Both should take ~100ms (bcrypt time). If one is <10ms, fix is not applied.

# Test OTP brute-force (Section 1.4):
for i in $(seq 1 6); do
  curl -X POST /api/v1/auth/register/verify -d '{"email":"x@x.com","code":"000000"}'
done
# 6th request should return 429, not 400

# Test IDOR — message access (Section 3.1):
# Login as user A, get match_id from user A's matches
# Login as user B (different account), try to GET /messages/{match_id}
# Should return 403, not 200

# Test file upload MIME (Section 4.1):
echo "not an image" > fake.jpg
curl -X POST /api/v1/users/me/photos -F "file=@fake.jpg"
# Should return 400, not 200
```
