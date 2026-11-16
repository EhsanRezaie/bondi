# Bondi — Technical Wiki

> Full technical reference for the backend (and the Flutter client contract).
> Consolidated from the former `dev.md`, `messaging.md`, the performance/scale/security
> plans and the old README internals. Deployment lives in [`DEPLOYMENT.md`](DEPLOYMENT.md);
> remaining work lives in [`TODO.md`](TODO.md); project intro lives in the root [`README.md`](../README.md).

## Table of Contents

**Part A — Project Reference** (from `dev.md` §1–§9)
- 1. Project Overview · 2. Team & Timeline · 3. Tech Stack
- 4. Repository Structure · 5. Environment & Configuration
- 6. Database Schema · 7. API Reference · 8. Architecture Decisions · 9. Business Rules

**Part B — Messaging & WebSocket Protocol** (from `messaging.md`, sections 1–14)

**Part C — Development Environment & Testing**
- Setup guides (macOS / Windows), verify setup, environment variables
- Running tests · Docker services · Bugsink (local error tracking)
- Testing strategy · Compose files (dev/test) · Alembic commands

**Part D — Current Hardening State** (what is already implemented)
- Scale state · Security inventory · Infrastructure checklist

---

# PART A — Project Reference

## 1. Project Overview

A **Persian-language dating app** for the Iranian market, similar to Badoo.

| Attribute | Detail |
|-----------|--------|
| Language | Persian (Farsi) UI, backend API in English |
| Target market | Iranian users worldwide |
| Orientation | Heterosexual only (male ↔ female) |
| Monetization | Premium subscriptions + rewarded ads (NO forced interstitial ads) |
| Primary platform | Android first, iOS later |

---

## 2. Team & Timeline

| Field | Detail |
|-------|--------|
| Developer | Ehsan (solo) |
| Backend expertise | Senior — FastAPI & Django |
| Mobile | Learning Flutter from scratch |
| Daily availability | 2–3 hours/day |
| Estimated MVP | 3–4 months with Claude assistance |

---

## 3. Tech Stack

| Layer | Tool |
|-------|------|
| API framework | FastAPI (async) |
| Database | PostgreSQL 15 |
| ORM | SQLAlchemy 2.x (async) |
| Migrations | Alembic |
| Cache | Redis 7 (tokens, rate limiting, verification codes) + LRU cache (static location data) |
| Realtime | WebSocket |
| File storage | MinIO (S3-compatible, self-hosted) — public/private bucket split by moderation status |
| Containerization | Docker + Docker Compose |
| Mobile | Flutter |
| Payment | ZarinPal (MOCKED - real integration needed) |
| Location | countrystatecity-countries package |
| Reverse Geocoding | Nominatim (OpenStreetMap) |
| Email | SMTP/SendGrid (TODO) |
| Encryption | AES-256-GCM (cryptography library) |

---

## 4. Repository Structure

```
iranian-dating-app/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── redis_client.py
│   │   │
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── session.py
│   │   │   ├── seed_data/
│   │   │   │   ├── interests.json             # 158 interests, 13 categories
│   │   │   │   └── prompts.json               # prompt templates
│   │   │   └── scripts/
│   │   │       ├── seed_interests.py          # Idempotent upsert seed/sync script
│   │   │       └── seed_dummy_users.py        # Generates dummy users from current models
│   │   │
│   │   ├── models/
│   │   │   ├── user.py                    # Core user model (auth only)
│   │   │   ├── user_profile.py            # User profile data (name, birth_date, gender, etc.)
│   │   │   ├── user_settings.py           # User settings (privacy, notifications)
│   │   │   ├── interest.py                # Available interests
│   │   │   ├── user_interest.py           # User interests (many-to-many)
│   │   │   ├── prompt.py                  # Available prompts/questions
│   │   │   ├── user_prompt.py             # User answers to prompts
│   │   │   ├── photo.py
│   │   │   ├── swipe.py
│   │   │   ├── match.py
│   │   │   ├── block.py
│   │   │   ├── message.py                 # ✅ Encrypted content support
│   │   │   ├── daily_limit.py
│   │   │   ├── review_reward.py
│   │   │   ├── subscription.py
│   │   │   ├── referral_reward.py
│   │   │   ├── notification.py
│   │   │   ├── report.py
│   │   │   └── ticket.py
│   │   │
│   │   ├── schemas/
│   │   │   ├── auth.py                    # 3-step registration schemas
│   │   │   ├── user.py                    # User profile schemas with all Badoo fields
│   │   │   ├── settings.py                # User settings schemas
│   │   │   ├── interest.py
│   │   │   ├── prompt.py
│   │   │   ├── photo.py
│   │   │   ├── discover.py
│   │   │   ├── search.py
│   │   │   ├── match.py
│   │   │   ├── message.py
│   │   │   ├── subscription.py
│   │   │   ├── rewards.py
│   │   │   ├── referral.py
│   │   │   ├── notification.py
│   │   │   ├── report.py
│   │   │   ├── ticket.py
│   │   │   ├── admin.py
│   │   │   ├── dashboard.py
│   │   │   ├── location.py
│   │   │   └── system.py                  # ✅ System status schemas
│   │   │
│   │   ├── api/v1/endpoints/
│   │   │   ├── auth.py                    # 3-step registration: init → verify → complete
│   │   │   ├── users.py                   # GET /me returns UserProfileResponse
│   │   │   ├── photos.py
│   │   │   ├── admin_photos.py
│   │   │   ├── discover.py                # ✅ Updated with gender filter, profile.age
│   │   │   ├── swipes.py
│   │   │   ├── search.py                  # ✅ Updated with profile.age
│   │   │   ├── matches.py
│   │   │   ├── blocks.py
│   │   │   ├── messages.py                # ✅ Updated with encryption
│   │   │   ├── admin_messages.py          # ✅ Admin decryption endpoints
│   │   │   ├── subscriptions.py
│   │   │   ├── rewards.py
│   │   │   ├── referrals.py
│   │   │   ├── notifications.py
│   │   │   ├── reports.py
│   │   │   ├── tickets.py
│   │   │   ├── admin_tickets.py
│   │   │   ├── admin_reports.py
│   │   │   ├── admin_users.py
│   │   │   ├── admin_dashboard.py
│   │   │   ├── admin_announcements.py
│   │   │   ├── admin_photos.py
│   │   │   ├── locations.py
│   │   │   └── system.py                  # ✅ System status & version check endpoints
│   │   │
│   │   ├── api/v1/websocket/
│   │   │   ├── matches.py
│   │   │   └── chat.py
│   │   │
│   │   ├── services/
│   │   │   ├── email_service.py           # Email sending (verification, password reset)
│   │   │   ├── reward_service.py
│   │   │   ├── notification_service.py
│   │   │   ├── chat_service.py            # ✅ Encryption-aware
│   │   │   ├── photo_service.py           # MinIO/S3 storage — upload, public/private bucket move, signed URLs
│   │   │   ├── media_service.py           # ✅ Updated with MinIO support
│   │   │   ├── websocket_manager.py
│   │   │   ├── nsfw_service.py              # ✅ NSFW photo detection (skin-tone heuristic)
│   │   │   └── location_service.py
│   │   │
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   ├── deps.py                    # get_current_user with profile & settings
│   │   │   ├── limiter.py
│   │   │   ├── logging.py
│   │   │   ├── redis.py                   # Refresh tokens + Verification codes
│   │   │   └── encryption.py              # ✅ AES-256-GCM encryption utilities
│   │   │
│   │   └── utils/
│   │       ├── geo.py
│   │       └── pagination.py
│   │
│   ├── alembic/versions/
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py                    # ✅ Auto-seeds interests, reset_state fixture
│   │   └── done/                          # 33 files, 584 tests, all ✅
│   │       ├── test_admin_dashboard.py
│   │       ├── test_admin_messages.py
│   │       ├── test_admin_photos.py
│   │       ├── test_admin_reports.py
│   │       ├── test_admin_tickets.py
│   │       ├── test_admin_users.py
│   │       ├── test_auth.py
│   │       ├── test_blocks.py
│   │       ├── test_daily_limits.py
│   │       ├── test_discover.py
│   │       ├── test_encryption.py
│   │       ├── test_interests.py
│   │       ├── test_locations.py
│   │       ├── test_matches.py
│   │       ├── test_messages.py
│   │       ├── test_messages_encryption.py
│   │       ├── test_notifications.py
│   │       ├── test_photos.py
│   │       ├── test_prompts.py
│   │       ├── test_referrals.py
│   │       ├── test_reports.py
│   │       ├── test_rewards.py
│   │       ├── test_search.py
│   │       ├── test_settings.py
│   │       ├── test_subscriptions.py
│   │       ├── test_swipes.py
│   │       ├── test_system.py
│   │       ├── test_tickets.py
│   │       ├── test_users.py
│   │       ├── test_nsfw.py                 # ✅ NSFW detection tests (service + endpoint + metrics)
│   │       └── test_websocket.py
│   │
│   ├── uploads/
│   ├── .env
│   ├── .env.example
│   ├── .env.test
│   ├── docker-compose.yml                 # App + all infrastructure (db, redis, minio, nginx, bondi_bugsink)
│   ├── docker-compose.test.yml            # Test infrastructure (db_test, redis_test, minio-test)
│   ├── pyproject.toml                     # Dependencies (uv) + pytest config
│   ├── uv.lock                            # Locked dependency graph
│   └── Dockerfile
│
└── mobile/                                # Flutter app
    ├── lib/
    │   ├── main.dart
    │   ├── config/
    │   │   ├── app_constants.dart
    │   │   └── app_theme.dart
    │   ├── models/
    │   │   ├── user.dart                  # Full Badoo fields + interests + prompts
    │   │   ├── interest.dart
    │   │   ├── prompt.dart
    │   │   ├── photo.dart
    │   │   └── location_models.dart
    │   ├── services/
    │   │   ├── api_service.dart           # Dio + interceptors
    │   │   ├── auth_service.dart          # 3-step registration + updateProfile + updateInterests + updatePrompts
    │   │   ├── storage_service.dart       # Token storage + userId
    │   │   ├── google_auth_service.dart   # Google Sign-In
    │   │   ├── location_service.dart      # GPS + location APIs
    │   │   ├── onboarding_service.dart
    │   │   └── photo_service.dart
    │   ├── providers/
    │   │   ├── auth_provider.dart         # Auth state + token persistence + updateProfile + updateInterests + updatePrompts
    │   │   ├── language_provider.dart
    │   │   ├── onboarding_provider.dart   # Multi-step profile data
    │   │   └── profile_provider.dart      # Profile state
    │   ├── screens/
    │   │   ├── splash_screen.dart
    │   │   ├── login_screen.dart          # Welcome + Login combined
    │   │   ├── main_screen.dart           # Bottom nav + onboarding check
    │   │   ├── auth/
    │   │   │   ├── sign_up_screen.dart    # Step 1: Email + Password
    │   │   │   └── verify_code_screen.dart # Step 2: OTP + Referral
    │   │   ├── onboarding/
    │   │   │   ├── basic_info_screen.dart
    │   │   │   ├── profile_details_screen.dart
    │   │   │   ├── interests_screen.dart
    │   │   │   ├── prompts_screen.dart
    │   │   │   └── photo_upload_screen.dart
    │   │   └── profile/
    │   │       ├── profile_screen.dart    # Account section with 6 menu items
    │   │       ├── avatar_crop_screen.dart
    │   │       ├── edit_basic_info_screen.dart
    │   │       ├── edit_profile_details_screen.dart
    │   │       ├── edit_interests_screen.dart
    │   │       └── edit_prompts_screen.dart
    │   ├── widgets/
    │   │   ├── loading_widget.dart
    │   │   └── progress_bar.dart
    │   ├── l10n/
    │   │   ├── app_en.arb                # English translations
    │   │   └── app_fa.arb                # Persian translations
    │   └── utils/
    │       └── validators.dart
    ├── pubspec.yaml
    └── .env
```

---

## 5. Environment & Configuration

### `.env` Variables

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/bondi
REDIS_URL=redis://localhost:6379

# Security
SECRET_KEY=your-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
ADMIN_SECRET_KEY=your-admin-key

# App
APP_NAME=DatingApp
DEBUG=True
APP_VERSION=1.0.0
ENVIRONMENT=development

# Daily Limits (ONLY restrictions in the app)
FREE_USER_DAILY_LIKES=20
FREE_USER_DAILY_CHATS=10

# Ad Rewards
AD_REWARD_LIKES_BONUS=5
AD_REWARD_CHATS_BONUS=3
MAX_AD_REWARDS_PER_DAY=2

# Bonuses
WELCOME_BONUS_DAYS=7
REFERRAL_INVITER_DAYS=3
REFERRAL_INVITED_DAYS=3

# Subscription Plans
SUBSCRIPTION_MONTHLY_DAYS=30
SUBSCRIPTION_QUARTERLY_DAYS=90
SUBSCRIPTION_YEARLY_DAYS=365
SUBSCRIPTION_QUARTERLY_DISCOUNT=15
SUBSCRIPTION_YEARLY_DISCOUNT=30

# Payment (MOCKED)
ZARINPAL_MERCHANT_ID=
ZARINPAL_SANDBOX=true
ZARINPAL_CALLBACK_URL=

# File Uploads (legacy — superseded by MinIO below)
MAX_PHOTO_SIZE_MB=10
MAX_PHOTOS_PER_USER=9

# MinIO / S3-compatible object storage (NO DEFAULTS — must be in .env)
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=us-east-1
S3_PUBLIC_BUCKET=photos-public
S3_PRIVATE_BUCKET=photos-private
S3_PUBLIC_BASE_URL=http://localhost:9000/photos-public
S3_SIGNED_URL_EXPIRE_SECONDS=900

# ===========================================
# Encryption
# ===========================================
ENCRYPTION_SECRET=your-super-secret-32-byte-key-here-change-in-production

# ============================================
# Chat Media Settings
# ============================================
MAX_CHAT_PHOTO_SIZE_MB=5
MAX_CHAT_VOICE_SIZE_MB=2
MAX_CHAT_VOICE_DURATION=120
ALLOWED_CHAT_IMAGE_FORMATS=JPEG,PNG,WEBP,JPG

# ============================================
# NSFW Detection
# ============================================
NSFW_ENABLED=true
NSFW_THRESHOLD=0.8

# ============================================
# Version Control
# ============================================
MIN_ANDROID_VERSION=1.0.0
MIN_IOS_VERSION=1.0.0
PLAY_STORE_URL=https://play.google.com/store/apps/details?id=your.app.id
APP_STORE_URL=https://apps.apple.com/app/your-app-id
FORCE_UPDATE_ENABLED=false
FORCE_UPDATE_MESSAGE=A critical update is available. Please update to continue using the app.

# ============================================
# FCM Push Notifications
# ============================================
FCM_SERVICE_ACCOUNT_PATH=firebase-service-account.json
```

### `.env.test`

```env
DATABASE_URL=postgresql+asyncpg://bondi_admin:CHANGE_ME@localhost:5433/bondi_test
REDIS_URL=redis://localhost:6380

SECRET_KEY=test-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
ADMIN_SECRET_KEY=test-admin-key

APP_NAME=DatingApp
DEBUG=True
APP_VERSION=1.0.0-test
ENVIRONMENT=test

FREE_USER_DAILY_LIKES=20
FREE_USER_DAILY_CHATS=10
AD_REWARD_LIKES_BONUS=5
AD_REWARD_CHATS_BONUS=3
MAX_AD_REWARDS_PER_DAY=2
WELCOME_BONUS_DAYS=7
REFERRAL_INVITER_DAYS=3
REFERRAL_INVITED_DAYS=3
SUBSCRIPTION_MONTHLY_DAYS=30
SUBSCRIPTION_QUARTERLY_DAYS=90
SUBSCRIPTION_YEARLY_DAYS=365
SUBSCRIPTION_QUARTERLY_DISCOUNT=15
SUBSCRIPTION_YEARLY_DISCOUNT=30

ZARINPAL_MERCHANT_ID=
ZARINPAL_SANDBOX=true
ZARINPAL_CALLBACK_URL=

MAX_PHOTO_SIZE_MB=5
MAX_PHOTOS_PER_USER=6

# MinIO / S3 — points at the minio-test service (docker-compose_test.yml)
S3_ENDPOINT_URL=http://localhost:9090
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=us-east-1
S3_PUBLIC_BUCKET=photos-public-test
S3_PRIVATE_BUCKET=photos-private-test
S3_PUBLIC_BASE_URL=http://localhost:9090/photos-public-test
S3_SIGNED_URL_EXPIRE_SECONDS=900

# ===========================================
# Encryption - Test
# ===========================================
ENCRYPTION_SECRET=test-encryption-secret-32-bytes-long-here

# ============================================
# Chat Media Settings - Test
# ============================================
MAX_CHAT_PHOTO_SIZE_MB=5
MAX_CHAT_VOICE_SIZE_MB=2
MAX_CHAT_VOICE_DURATION=120
ALLOWED_CHAT_IMAGE_FORMATS=JPEG,PNG,WEBP,JPG

# ============================================
# Version Control - Test
# ============================================
MIN_ANDROID_VERSION=1.0.0
MIN_IOS_VERSION=1.0.0
PLAY_STORE_URL=https://play.google.com/store/apps/details?id=your.app.id
APP_STORE_URL=https://apps.apple.com/app/your-app-id
FORCE_UPDATE_ENABLED=false
FORCE_UPDATE_MESSAGE=A critical update is available. Please update to continue using the app.
```

---

## 6. Database Schema

### `users` Table (Core - Authentication)

| Column | Type |
|--------|------|
| id | UUID |
| email | VARCHAR(255) UNIQUE |
| password_hash | VARCHAR(255) |
| google_id | VARCHAR(255) UNIQUE |
| phone | VARCHAR(20) |
| phone_verified | BOOLEAN |
| is_active | BOOLEAN |
| token_version | INTEGER |
| registration_status | VARCHAR(20) |
| referral_code | VARCHAR(20) UNIQUE |
| referred_by | UUID |
| created_at | TIMESTAMPTZ |
| last_seen_at | TIMESTAMPTZ |

### `user_profiles` Table (All Profile Data)

| Column | Type |
|--------|------|
| id | UUID |
| user_id | UUID (FK → users) |
| name | VARCHAR(100) |
| birth_date | DATE |
| gender | VARCHAR(10) |
| sexual_orientation | VARCHAR(20) |
| bio | TEXT |
| height | SMALLINT |
| weight | SMALLINT |
| body_type | VARCHAR(20) |
| relationship_status | VARCHAR(20) |
| living_situation | VARCHAR(30) |
| children_status | VARCHAR(20) |
| smoking | VARCHAR(20) |
| drinking | VARCHAR(20) |
| languages | JSON |
| education | VARCHAR(50) |
| workplace | VARCHAR(100) |
| religion | VARCHAR(50) |
| ethnicity | VARCHAR(50) |
| political_orientation | VARCHAR(30) |
| lat | DOUBLE |
| lng | DOUBLE |
| country | VARCHAR(100) |
| province | VARCHAR(100) |
| city | VARCHAR(100) |
| location_manual | BOOLEAN |
| is_verified | BOOLEAN |
| premium_until | TIMESTAMPTZ |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

### `user_settings` Table

| Column | Type |
|--------|------|
| id | UUID |
| user_id | UUID (FK → users) |
| hide_last_seen | BOOLEAN |
| hide_online_status | BOOLEAN |
| push_enabled | BOOLEAN |
| like_notifications | BOOLEAN |
| match_notifications | BOOLEAN |
| message_notifications | BOOLEAN |
| language | VARCHAR(10) |
| dark_mode | BOOLEAN |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

### `messages` Table

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | |
| match_id | UUID (FK → matches) | Nullable for unmatched chats |
| sender_id | UUID (FK → users) | |
| receiver_id | UUID (FK → users) | |
| message_type | VARCHAR(20) | text, photo, voice |
| **content** | **TEXT** | **✅ ENCRYPTED** - AES-256-GCM |
| reply_to_id | UUID (FK → messages) | |
| media_url | TEXT | MinIO object key |
| media_duration | INTEGER | Voice duration |
| media_size | INTEGER | File size |
| is_sent | BOOLEAN | |
| is_delivered | BOOLEAN | |
| is_read | BOOLEAN | |
| is_deleted_for_sender | BOOLEAN | |
| is_deleted_for_receiver | BOOLEAN | |
| is_deleted_for_all | BOOLEAN | |
| deleted_at | TIMESTAMPTZ | |
| is_accepted | BOOLEAN | Unmatched chat acceptance |
| sent_at | TIMESTAMPTZ | |
| delivered_at | TIMESTAMPTZ | |
| read_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### Other Tables (Unchanged)

| Table | Purpose |
|-------|---------|
| swipes | Like/pass records |
| matches | Mutual likes |
| blocks | Blocked users |
| daily_limits | Daily likes/chats usage |
| subscriptions | Premium subscriptions |
| referral_rewards | Referral rewards |
| notifications | In-app notifications |
| reports | User reports |
| tickets | Support tickets |

### `device_tokens` Table (Push Notifications)

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | Primary key |
| user_id | UUID (FK → users) | CASCADE delete |
| token | VARCHAR | FCM registration token |
| platform | VARCHAR(10) | "android" or "ios" |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Constraints: unique(`user_id`, `token`), index on `user_id`

---

## 7. API Reference

### Authentication Flow (3-Step Registration)

| Step | Endpoint | Method | Description |
|------|----------|--------|-------------|
| 1 | `/auth/register/init` | POST | Check email, send verification code |
| 2 | `/auth/register/verify` | POST | Verify code, create user with email+password |
| 3 | `/auth/register/complete` | POST | Complete profile with all fields |

### Auth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/register/init` | POST | Email exists check + send code |
| `/auth/register/verify` | POST | Verify code + create user |
| `/auth/register/complete` | POST | Complete profile |
| `/auth/login` | POST | Login with email + password |
| `/auth/google` | POST | Google OAuth login |
| `/auth/refresh` | POST | Refresh access token |
| `/auth/logout` | POST | Logout (revoke refresh token) |
| `/auth/change-password` | POST | Change password |
| `/auth/password-reset` | POST | Request password reset |
| `/auth/password-reset/verify` | POST | Verify reset code + set new password |
| `/auth/health` | GET | Health check |

### User Endpoints

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `/users/me` | GET | Get current user profile | ✅ |
| `/users/me` | PUT | Update profile | ✅ |
| `/users/me/interests` | PUT | Update interests | ✅ |
| `/users/me/prompts` | PUT | Update prompts | ✅ |
| `/users/me` | DELETE | Soft delete account | ✅ |
| `/users/me/location` | POST | Update GPS location | ✅ |
| `/users/me/location-text` | PATCH | Update text location | ✅ |
| `/users/me/photos` | GET | Get all photos | ✅ |
| `/users/me/photos` | POST | Upload photo (triggers NSFW check, auto-rejects if score ≥ threshold) | ✅ |
| `/users/me/photos/{id}` | DELETE | Delete photo | ✅ |
| `/users/me/photos/{id}/main` | PUT | Set main photo | ✅ |

### Discover Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/discover` | GET | Discover users with filters (gender optional, age, distance) |
| `/discover` | GET | Supports `gender` filter (male/female) - if not provided, shows all genders |

### Search Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search` | GET | Search users with advanced filters |
| `/search` | GET | Supports all profile fields: age, gender, height, weight, country, province, city, religion, ethnicity, relationship_status, body_type, education, smoking, drinking, political_orientation, languages, interests |
| `/search` | GET | Supports pagination with `limit` (≤50), `offset` (≤1000 — for depth use `next_cursor`), and `cursor` |
| `/search` | GET | Supports sorting by recent, distance, age, name, last_seen |

**⚠️ Breaking-ish changes (pre-launch, 2026-11):**
- **Default sort `recent` now means "most recently active"** (`last_seen_at DESC`, same ordering as discover) — it previously sorted by `created_at`. The `recent` key is still accepted; `last_seen` is the explicit alias.
- **`total` is capped at 2001.** When more users match the filters, the API returns `total: 2001` — render as **"2000+"**. Exact totals above 2000 are no longer provided (deep paging is unaffected — follow `next_cursor`).
- `offset` is validated `≤ 1000` — requests with a larger offset return 422.

### Swipes Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/swipes` | POST | Swipe right (like) or left (pass) on a user |
| `/swipes/stats` | GET | Get swipe statistics for current user |
| `/swipes/liked` | GET | Get paginated list of users the current user has liked |
| `/swipes/likers` | GET | Get paginated list of users who liked the current user (excludes matches) |

### Interests Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/interests` | GET | Public endpoint, returns all 158 interests sorted by category, name |

### Prompts Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/prompts` | GET | Public endpoint, returns active prompts in requested language (en/fa) |

### Location Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/locations/countries` | GET | Get all countries |
| `/locations/states` | GET | Get states/provinces for a country |
| `/locations/cities` | GET | Get cities for a country/state |
| `/locations/reverse-geocode` | GET | Convert GPS to location text |
| `/locations/city-centroid` | GET | Get lat/lng for a city |

### System Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/system/status` | GET | System status (services, maintenance, version) - splash screen |
| `/system/version-check` | POST | Check app version compatibility |
| `/system/maintenance/enable` | POST | Admin - enable maintenance mode |
| `/system/maintenance/disable` | POST | Admin - disable maintenance mode |
| `/system/maintenance/status` | GET | Admin - get maintenance status |
| `/system/version/set-minimum` | POST | Admin - set minimum version per platform |
| `/system/version/force-update` | POST | Admin - enable/disable force update |
| `/system/version/config` | GET | Admin - get version configuration |
| `/system/version/override` | DELETE | Admin - clear version overrides |

### Messages Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/messages/{identifier}` | GET | Get chat history (decrypted) |
| `/messages/{identifier}/text` | POST | Send text message (encrypted, sends push) |
| `/messages/{identifier}/photo` | POST | Send photo message (caption encrypted) |
| `/messages/{identifier}/voice` | POST | Send voice message |
| `/messages/{identifier}/accept` | POST | Accept unmatched chat |
| `/messages/delivered` | POST | Mark messages as delivered |
| `/messages/read` | POST | Mark messages as read |
| `/messages/{message_id}` | DELETE | Delete message |
| `/messages/{message_id}/forward` | POST | Forward message (re-encrypted) |
| `/messages/{message_id}/status` | GET | Get message status |

### Push Notification Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/notifications/device-token` | POST | Register/update FCM device token |
| `/notifications/device-token/{id}` | DELETE | Remove device token |

### Admin Messages Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/messages/{message_id}/decrypt` | GET | Admin decrypt message |
| `/admin/messages/{message_id}` | DELETE | Admin delete message |
| `/admin/messages/reports/{report_id}/message` | GET | View reported message |

---

## 8. Architecture Decisions

### Profile Edit & Account Settings Architecture (Session 21 - Mobile)

**Profile Screen Updates:**
- Account section with 6 menu items:
  1. **Verify Picture** - Shows verification status (`face_verified` from `PhotoResponse`)
  2. **Basic Info** - Navigate to `EditBasicInfoScreen`
  3. **Profile Details** - Navigate to `EditProfileDetailsScreen`
  4. **Interests** - Navigate to `EditInterestsScreen`
  5. **Prompts** - Navigate to `EditPromptsScreen`
  6. **Edit Photos** - Navigate to photo management (coming soon)
- Logout button removed from ProfileScreen (moved to Settings)

**Edit Screens Architecture:**
- Each edit screen reuses onboarding UI components
- Pre-filled with user data from `AuthProvider.user`
- Separate API calls for each section:
  - `PUT /users/me` - Update basic info and profile details
  - `PUT /users/me/interests` - Update interests
  - `PUT /users/me/prompts` - Update prompts
- No progress bars (these are edit screens, not onboarding)
- Back arrow navigation
- Full-width Save button (no Cancel button)

**User Model Updates:**
- Added `birthDate` field to `User` model
- Added `interests` field (List<String>) to `User` model
- Added `promptsData` field (List<Map<String, dynamic>>) to `User` model
- Backend returns `prompts_data` field in `UserProfileResponse`

**Backend Updates:**
- Added `interests` and `prompts_data` fields to `UserProfileResponse` schema
- Added `PUT /users/me/interests` endpoint
- Added `PUT /users/me/prompts` endpoint
- Fixed prompts validator using `values.__dict__['prompts']` to bypass SQLAlchemy descriptor conflict
- Added `InterestUpdateRequest` and `PromptUpdateRequest` schemas

**Location Update Flow:**
- EditBasicInfoScreen uses three separate API calls:
  1. `PUT /users/me` - Update profile fields (name, gender, bio, birth_date)
  2. `PATCH /users/me/location-text` - Update location text (country, province, city)
  3. `POST /users/me/location` - Update GPS coordinates (lat, lng)

**Enum Mapping:**
- Profile details fields map UI display values to backend enum values
- Removed invalid options that don't match backend enums:
  - Relationship: only 'Single', 'Divorced', 'Widowed', 'Separated'
  - Children: removed 'Open to children'
  - Smoking: removed 'Trying to quit', 'Socially' → 'occasionally'
  - Drinking: removed 'Sober'
  - Education: removed 'In College'
  - Political: removed 'Other'

### System Status & Version Check Architecture (Session 24)

**System Status Endpoint (`/system/status`):**
- Public endpoint for splash screen
- Checks: Database, Redis, MinIO connectivity
- Returns maintenance mode status
- Returns app version and environment
- Rate limited: 60/minute

**Version Check Endpoint (`/system/version-check`):**
- Called on splash screen before app loads
- Checks if app version meets minimum requirements
- Supports: `android` and `ios` platforms
- Returns: `ok`, `update_required`, or `maintenance`
- Force update capability for critical updates
- Runtime overrides via `version_override.json`

**Maintenance Mode:**
- Admin-controlled via `X-Admin-Key` header
- Persisted in `maintenance.json` file
- Blocks all API requests when enabled
- Shows maintenance message to users

**Version Control:**
- Settings-based: `MIN_ANDROID_VERSION`, `MIN_IOS_VERSION`
- Runtime overrides: `version_override.json`
- Admin API for dynamic version management
- Store links: Play Store & App Store URLs

**Files Created:**
- `app/schemas/system.py` - System schemas
- `app/api/v1/endpoints/system.py` - System endpoints
- `tests/test_system.py` - 24 tests passing

**Files Modified:**
- `app/core/config.py` - Added version settings
- `.env` / `.env.test` - Added version variables

### Message Encryption Architecture (Session 22)

**Server-side AES-256-GCM encryption** for all chat messages:

- **Encryption Algorithm:** AES-256-GCM (authenticated encryption with associated data)
- **Key Derivation:** PBKDF2 with 100,000 iterations
- **Key Material:** `match_id + ENCRYPTION_SECRET`
- **Key Storage:** Keys are NEVER stored - derived on-the-fly
- **Database Storage:** Encrypted content stored in `_content` column
- **Decryption:** Automatic via SQLAlchemy property getter

**Encryption Flow:**
1. Message sent → `match_id` used with `ENCRYPTION_SECRET` to derive key
2. Content encrypted with AES-256-GCM → stored in database
3. On retrieval → content decrypted automatically via property
4. Admin can decrypt via admin endpoints

**Security Benefits:**
- ✅ Database theft exposes only encrypted data
- ✅ Each chat has unique encryption key
- ✅ Admin can decrypt for moderation purposes
- ✅ Client receives decrypted content via API/WebSocket

**Encrypted Content Types:**
- Text messages (full content)
- Photo captions
- Voice message metadata (duration not encrypted)

**Admin Moderation:**
- Admin can decrypt messages via `/api/v1/admin/messages/{id}/decrypt`
- Admin can delete offensive messages
- Admin can view reported messages

**Files Created:**
- `app/core/encryption.py` - Core encryption utilities
- `app/api/v1/endpoints/admin_messages.py` - Admin decryption endpoints

**Files Modified:**
- `app/models/message.py` - Encrypted content property
- `app/services/chat_service.py` - Encryption-aware functions
- `app/api/v1/endpoints/messages.py` - Updated for encryption
- `app/core/config.py` - Added ENCRYPTION_SECRET

### Photo Storage Architecture (MinIO / S3)

Replaced local-disk `uploads/` storage with MinIO (self-hosted, S3-compatible), chosen over managed cloud storage (S3/R2) because the app deploys to a self-managed VPS — MinIO runs in the same Docker Compose stack as Postgres/Redis, no external account or cross-border data dependency.

**Two-bucket split by moderation status:**

| Bucket | Holds | Access |
|--------|-------|--------|
| `photos-private` | `pending`, `rejected` photos | Signed URLs only (15 min expiry), owner can still view their own |
| `photos-public` | `approved` photos | Plain public URL, anonymous read (bucket policy via `mc anonymous set download`) |

- `Photo.url` stores the object **key** only (e.g. `users/{id}/{photo_id}.jpg`), never a full URL
- On admin approval, `PhotoService.publish_photo()` copies the object from `photos-private` → `photos-public` and deletes the original
- `PhotoService.get_photo_url(key, status)` resolves the correct URL at read time: public URL if `approved`, signed private URL otherwise

### Push Notification Architecture (FCM)

Firebase Cloud Messaging for real-time push notifications on Android/iOS.

**Push Triggers:**

| Event | Recipients | Title |
|-------|-----------|-------|
| Like | Liked user only | "Someone liked you!" |
| Match | Both matched users | "It's a match!" |
| Message | Receiver only | "New message" |

**Architecture:**
- `PushService.send_to_user()` — static method, looks up `DeviceToken` records, sends `MulticastMessage` via Firebase Admin SDK
- Lazy Firebase init — only initializes on first send, gracefully no-ops if `FCM_SERVICE_ACCOUNT_PATH` not set
- Auto-cleanup of invalid tokens (`registration-token-not-registered`, `invalid-registration-token`)
- `NotificationService` calls `PushService` after creating DB notification records

**Files Created:**
| File | Purpose |
|------|---------|
| `app/services/push_service.py` | FCM send + token cleanup |
| `app/models/device_token.py` | `device_tokens` table (user_id, token, platform) |

**Files Modified:**
| File | Changes |
|------|---------|
| `app/services/notification_service.py` | Added `PushService.send_to_user()` calls in `notify_like()`, `notify_match()`, `notify_message()` |
| `app/api/v1/endpoints/notifications.py` | Added POST/DELETE `/device-token` endpoints |
| `app/api/v1/endpoints/messages.py` | Added `NotificationService.notify_message()` call after text message creation |
| `app/models/user.py` | Added `device_tokens` relationship |
| `app/core/config.py` | Added `FCM_SERVICE_ACCOUNT_PATH` setting |
| `requirements.txt` | Added `firebase-admin==6.8.0` |

### Discover & Search Architecture (Session 23)

**Discover Endpoint (`/discover`):**
- Returns users for swiping
- Optional `gender` filter - if not provided, shows all genders
- Age filter using `birth_date.between()` with `profile.age` for response
- Excludes: swiped users (like/pass), blocked users, users who blocked you, already matched users
- Pagination with `limit` and `offset`
- Distance filter using Haversine formula — `distance_km` is optional (default `None`); when omitted, returns all users regardless of distance. When provided, filters to that radius.

**Search Endpoint (`/search`):**
- Advanced filters: age, gender, height, weight, country, province, city, religion, ethnicity, relationship_status, body_type, education, smoking, drinking, political_orientation, languages, interests
- Multi-value filters: languages (AND condition), interests (AND condition)
- Excludes: blocked users (both directions)
- Sorting: recent, distance, age, name
- Pagination with `limit` and `offset`
- Uses `profile.age` property for response age display

**Files Updated (Session 23):**
- `app/api/v1/endpoints/discover.py` - Gender filter, profile.age, block exclusions, matched exclusions
- `app/api/v1/endpoints/search.py` - profile.age, block exclusions (both directions)
- `tests/test_discover.py` - 23 tests passing
- `tests/test_search.py` - 38 tests passing
- `tests/test_blocks.py` - All passing

### Interests Endpoint (Session 23)

- Public endpoint (no auth required)
- Returns all 158 interests from `interests.json`
- Sorted by `category` then `name`
- Used during onboarding before user has a token
- Flutter resolves localized display names client-side

**Test Strategy:**
- `conftest.py` auto-seeds interests in `setup_database`
- `reset_state` re-seeds interests after each test
- `test_interests.py` - 21 tests passing

### NSFW Photo Moderation Architecture

Automated NSFW detection on photo uploads using a lightweight skin-tone heuristic.

**Flow:**
```
Upload → validate_image() → NSFW check → save_photo() → status="pending"
                                              ↓ (if score ≥ threshold)
                                         status="rejected"
                                         quarantine to S3
                                         log + metrics
```

**Detection method:** Skin-tone HSV analysis — calculates the ratio of skin-colored pixels in the image. Higher skin ratio → higher NSFW score (0.0 safe → 1.0 explicit). This is a simple baseline; swap for ML model (opennsfw2/TensorFlow) in production.

**Configuration:**
- `NSFW_ENABLED`: Enable/disable detection (default: `true`)
- `NSFW_THRESHOLD`: Score cutoff for auto-rejection (default: `0.8`)
- Fail-open on errors — processing failures log but allow the image

**Quarantine storage:** Rejected photos saved to S3 private bucket under `quarantine/{user_id}/{photo_id}.jpg` — accessible via admin endpoints for review.

**Files:**
| File | Purpose |
|------|---------|
| `app/services/nsfw_service.py` | NSFW detection service (skin-tone heuristic) |
| `app/api/v1/endpoints/photos.py` | NSFW check after image validation |
| `app/models/photo.py` | `nsfw_score` column |
| `app/core/config.py` | `NSFW_ENABLED`, `NSFW_THRESHOLD` settings |

### Authentication Flow (3-Step)

```
Step 1: POST /auth/register/init
   ↓
   Check email → send 6-digit code (Redis, TTL 5min)
   ↓
Step 2: POST /auth/register/verify
   ↓
   Verify code → create User (email, password_hash, registration_status="email_verified")
   ↓
   Return access_token + refresh_token
   ↓
Step 3: POST /auth/register/complete (Authenticated)
   ↓
   Save all profile fields → registration_status="onboarding_complete"
   ↓
   Return new tokens with full profile
```

### Token Management

- Access Token: JWT (7 days) with `ver` (token_version)
- Refresh Token: Opaque (Redis, 30 days)
- Token Rotation: Old refresh token revoked on refresh
- Password Change: Increment token_version, revoke all tokens

### Redis Keys

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `refresh_token:{token}` | Store user_id | 30 days |
| `verification:{email}` | Store 6-digit code | 5 minutes |

---

## 9. Business Rules

### Free User Daily Limits (ONLY RESTRICTIONS)

| Action | Daily Limit | Ad Bonus (max 2x/day) |
|--------|-------------|----------------------|
| Likes | 20 | +5 each |
| New Chats | 10 | +3 each |

### Premium User

- Unlimited likes
- Unlimited new chats
- Same everything else as free users

### Rewards (All Users)

| Event | Reward |
|-------|--------|
| New registration | 7 days premium (welcome bonus) |
| Referral (inviter) | +3 days premium |
| Referral (invited) | +3 days premium |
| Watch ad | +5 likes, +3 chats (max 2 ads/day) |

### Subscription Plans (MOCKED)

| Plan | Duration | Discount |
|------|----------|----------|
| Monthly | 30 days | 0% |
| Quarterly | 90 days | 15% |
| Yearly | 365 days | 30% |

### Block Rules

| Scenario | Result |
|----------|--------|
| You block someone | ❌ They are excluded from your search/discover |
| Someone blocks you | ❌ They are excluded from your search/discover |
| No block relationship | ✅ They appear in your search/discover |

---

---

# PART B — Messaging & WebSocket Protocol

# Messaging & WebSocket Guide

## Iranian Dating App — Backend + Flutter

> Complete reference for the chat messaging system.
> Covers all HTTP REST endpoints and WebSocket events.
> Written for Flutter mobile developers integrating with the Bondi backend.

---

## 1. Overview

The messaging system uses **two transport layers** that work together:

| Layer | Transport | Purpose |
|-------|-----------|---------|
| **HTTP REST** | HTTPS POST/GET/DELETE | Persist messages to DB, enforce auth, rate limits, encryption |
| **WebSocket** | WS (wss:// in prod) | Real-time push delivery to the recipient, typing indicators, presence |

### Why both are needed

- **HTTP POST** handles the heavy lifting: validation, encryption, DB storage, rate limiting, push notifications. It is the authoritative write operation.
- **WebSocket** is the real-time notification pipe: it pushes new messages to the recipient instantly without polling. It also streams typing indicators, online/offline status, and read receipts.

If you only used HTTP, the recipient would need to poll `GET /messages/{id}` every few seconds — wasting battery and bandwidth. If you only used WebSocket, messages would be lost when connections drop (no persistent storage).

---

## 2. Architecture Diagram

```
Flutter App                    Backend                         Redis / DB
───────                       ───────                         ──────────
   │                              │
   │  POST /messages/{id}/text   │
   │  (send text message) ─────► │
   │                              │  ├─ Validates, encrypts, stores in PostgreSQL
   │  ◄── 200 SendMessageResponse│  └─ Enqueues BackgroundTask for WebSocket push
   │                              │
   │                              │  ── BackgroundTask ──────────────────┐
   │                              │  publish to Redis Pub/Sub           │
   │                              │  channel: ws:chat:{match_id} ──────►│
   │                              │  ┌──────────────────────────────────┐│
   │                              │  │  WebSocket worker (any process) ││
   │                              │  │  delivers to recipient's socket ││
   │                              │  └──────────────────────────────────┘│
   │                              │
   │  WS /ws/chat/{matchId} ◄────│  (persistent connection)
   │  receives real-time events  │
   │  ├─ new_message             │
   │  ├─ typing                  │
   │  ├─ typing_stopped          │
   │  ├─ messages_read           │
   │  ├─ user_online             │
   │  ├─ user_offline            │
   │  └─ pong (to ping)          │
```

---

## 3. HTTP API Endpoints

All message endpoints are prefixed with `/api/v1` and require authentication (`Authorization: Bearer <JWT>`).

### 3.1 Send Text Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/text` |
| **Content-Type** | `application/json` |
| **Rate Limit** | 60/min (plus 30/min per-match Redis limit) |

**Request Body** (`TextMessageRequest`):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `content` | `string` | Yes | 1–5000 characters |
| `reply_to_id` | `UUID` | No | ID of a message to reply to |

**Example Request:**
```json
{
  "content": "Hey! How are you?",
  "reply_to_id": null
}
```

**Response** (`SendMessageResponse`) — 200:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | The new message ID |
| `sent_at` | `ISO datetime` | When the message was sent |
| `requires_acceptance` | `bool` | True if unmatched chat, not yet accepted |
| `chat_accepted` | `bool` | True if chat is active (matched or accepted) |
| `chats_remaining_today` | `int \| null` | Remaining new chats for free users |

**Example Response:**
```json
{
  "id": "a1b2c3d4-...",
  "sent_at": "2026-07-31T12:00:00Z",
  "requires_acceptance": false,
  "chat_accepted": true,
  "chats_remaining_today": null
}
```

---

### 3.2 Send Photo Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/photo` |
| **Content-Type** | `multipart/form-data` |
| **Rate Limit** | 30/min |

**Request Body** (multipart form):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `file` | `binary` (image) | Yes | JPEG, PNG, WEBP, JPG; ≤ `MAX_CHAT_PHOTO_SIZE_MB` |
| `caption` | `string` | No | Max 500 characters |

**Example (Flutter):**
```dart
var request = http.MultipartRequest(
  'POST',
  Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/photo'),
);
request.fields['caption'] = 'Look at this!';
request.files.add(await http.MultipartFile.fromPath('file', imagePath));
```

**Response** (`SendMessageResponse`) — 200 (same shape as text, `requires_acceptance` is always `false`, `chat_accepted` is always `true`)

---

### 3.3 Send Voice Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/voice` |
| **Content-Type** | `multipart/form-data` |
| **Rate Limit** | 30/min |

**Request Body** (multipart form):

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `file` | `binary` (audio) | Yes | MP3, ≤ `MAX_CHAT_VOICE_SIZE_MB`, duration ≤ `MAX_CHAT_VOICE_DURATION` (120s) |
| `duration` | `integer` | Yes | Voice duration in seconds (1–120) |

**Example:**
```dart
request.fields['duration'] = '45';
request.files.add(await http.MultipartFile.fromPath('file', voicePath));
```

**Response** (`SendMessageResponse`) — 200 (same as photo)

---

### 3.4 Get Chat History

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/v1/messages/{identifier}` |
| **Auth** | Required |
| **Rate Limit** | 60/min |

**Path Parameter:**

| Param | Type | Description |
|-------|------|-------------|
| `identifier` | `UUID` | Either a **match_id** (for matched chats) or a **user_id** (for unmatched chats) |

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `int` | 30 | Messages per page (1–50) |
| `offset` | `int` | 0 | Legacy offset pagination |
| `before` | `ISO datetime` | — | Cursor pagination: fetch messages older than this timestamp |

**Cursor Pagination (Recommended):**
- Load the first page with no `before` parameter
- Take the `sent_at` of the oldest message in the response
- Pass it as `before` for the next page

**Response** (`MessageListResponse`):

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list` | Array of `MessageResponse` objects (oldest first) |
| `total` | `int` | Total message count in this chat |
| `next_offset` | `int \| null` | Offset for next page (null if no more) |

**`MessageResponse` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | Message ID |
| `match_id` | `UUID \| null` | Match ID (null for unmatched chats) |
| `sender_id` | `UUID` | Who sent this message |
| `receiver_id` | `UUID` | Who received this message |
| `message_type` | `str` | `"text"`, `"photo"`, or `"voice"` |
| `content` | `str \| null` | Decrypted text content (null for photo/voice) |
| `media_url` | `str \| null` | Signed URL for photo/voice media |
| `media_duration` | `int \| null` | Duration in seconds (voice only) |
| `reply_to` | `object \| null` | `ReplyToResponse` if this is a reply |
| `is_sent` | `bool` | Message was sent by the requesting user |
| `is_delivered` | `bool` | Received by the other user |
| `is_read` | `bool` | The other user has read it |
| `is_accepted` | `bool` | Chat acceptance status (unmatched chats) |
| `sent_at` | `ISO datetime` | |
| `delivered_at` | `ISO datetime \| null` | |
| `read_at` | `ISO datetime \| null` | |

---

### 3.5 Mark Messages as Delivered

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/delivered` |
| **Rate Limit** | 100/min |

**Request Body** (`MarkReadRequest`):

| Field | Type | Required |
|-------|------|----------|
| `message_ids` | `list[UUID]` | Yes |

**Response:** `{"message": "N messages marked as delivered"}`

---

### 3.6 Mark Messages as Read

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/read` |
| **Rate Limit** | 100/min |

**Request Body:** Same as delivered — `{"message_ids": [uuid1, uuid2, ...]}`

**Response:** `{"message": "N messages marked as read"}`

---

### 3.7 Accept Unmatched Chat

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{identifier}/accept` |
| **Rate Limit** | 20/min |

**Description:** Converts an unmatched chat to an accepted (unlimited) chat.

**Response** (`AcceptChatResponse`):
```json
{
  "message": "Chat accepted. You can now send unlimited messages.",
  "is_accepted": true
}
```

---

### 3.8 Delete a Message

| Field | Value |
|-------|-------|
| **Method** | `DELETE` |
| **Path** | `/api/v1/messages/{message_id}` |
| **Rate Limit** | 30/min |

**Query Parameter:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `delete_for` | `string` | `"me"` | `"me"` = delete for self, `"everyone"` = delete for both (sender only, <1 hour old) |

**Response:** `{"message": "Message deleted for me"}`

---

### 3.9 Forward a Message

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/v1/messages/{message_id}/forward` |
| **Rate Limit** | 30/min |

**Request Body** (`ForwardMessageRequest`):

| Field | Type | Required |
|-------|------|----------|
| `target_match_id` | `UUID` | Yes — the match to forward to |

**Response** (`ForwardMessageResponse`):
```json
{
  "message": "Message forwarded",
  "new_message_id": "uuid-of-new-message"
}
```

---

### 3.10 Get Message Status

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/v1/messages/{message_id}/status` |
| **Rate Limit** | 60/min |

**Response** (`MessageStatusResponse`):

| Field | Type | Description |
|-------|------|-------------|
| `id` | `UUID` | |
| `sent_at` | `ISO datetime` | |
| `delivered_at` | `ISO datetime \| null` | |
| `read_at` | `ISO datetime \| null` | |
| `is_delivered` | `bool` | |
| `is_read` | `bool` | |

---

## 4. WebSocket Connection

### 4.1 URL and Auth

| Property | Value |
|----------|-------|
| **Path** | `/ws/chat/{match_id}` (no `/api/v1` prefix) |
| **Protocol** | `ws://` (dev) or `wss://` (production) |
| **Auth** | JWT via query parameter `?token=<JWT>` |
| **Example** | `wss://api.bondi.ir/ws/chat/a1b2c3d4-...?token=<JWT>` |

### 4.2 Connection Flow

```
1. Client connects: ws://host/ws/chat/{match_id}?token={jwt}
2. Server decodes JWT, extracts user_id from "sub" claim
   → If invalid/expired: close code 4001, reason "Unauthorized"
3. Server checks match exists (is_active=true), user is a participant
   → If not found or not a participant: close code 4003, reason "Access denied"
4. Server accepts the WebSocket connection
5. Server:
   - Adds user to chat channel (Redis Pub/Sub: ws:chat:{match_id})
   - Sets online status: Redis key online:{user_id}, TTL 60s
   - Starts listener task for this match's channel
   - Broadcasts user_online event to the other participant
```

### 4.3 Disconnect / Timeout

- If no message is received for **40 seconds**, the server closes the connection.
- On disconnect, the server broadcasts `user_offline` and clears typing status.

---

## 5. Client → Server WS Messages

All messages are sent as JSON text frames over the WebSocket.

### 5.1 Ping / Keepalive

```json
{"type": "ping"}
```

The server responds with `{"type": "pong"}`. Use this to keep the connection alive and refresh online presence (60s TTL). Send every 30 seconds.

### 5.2 Typing Started

Send when the user begins typing:

```json
{"type": "typing"}
```

### 5.3 Typing Stopped

Send when the user stops typing or sends a message:

```json
{"type": "typing_stopped"}
```

### 5.4 Read Receipts

Send when the user reads messages:

```json
{
  "type": "read",
  "message_ids": ["uuid1", "uuid2", "uuid3"]
}
```

> **Note:** The WebSocket `read` event notifies the other user in real-time. The database update (`is_read=True`, `read_at=now()`) is done separately via the HTTP endpoint `POST /api/v1/messages/read`.

---

## 6. Server → Client WS Messages

All events are JSON objects with a top-level `type` field and a `data` payload.

### 6.1 New Message (`new_message`)

Sent to both participants when a message is sent by either user.

**Text message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "text",
    "content": "Hey! How are you?",
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

**Photo message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "photo",
    "media_url": "https://minio-host/photos-private/...?sig=...",
    "caption": "Look at this!",
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

**Voice message:**
```json
{
  "type": "new_message",
  "data": {
    "id": "a1b2c3d4-...",
    "message_type": "voice",
    "media_url": "https://minio-host/photos-private/...?sig=...",
    "duration": 45,
    "sender_id": "user-uuid",
    "sent_at": "2026-07-31T12:00:00Z"
  }
}
```

| Field | Text | Photo | Voice |
|-------|------|-------|-------|
| `content` | ✅ decrypted text | ✅ caption | ❌ absent |
| `media_url` | ❌ absent | ✅ signed URL | ✅ signed URL |
| `duration` | ❌ absent | ❌ absent | ✅ seconds |

> **Important:** `media_url` is a signed URL with a 15-minute expiry. Display the photo/voice immediately — do not cache the URL long-term.

### 6.2 Match Notification (`new_match`)

Sent when a mutual like creates a new match.

```json
{
  "type": "new_match",
  "data": {
    "match_id": "match-uuid",
    "user": {
      "id": "other-user-uuid",
      "name": "Sara",
      "age": 28,
      "main_photo_url": "https://minio-host/photos-public/...",
      "_note": "main_photo_url may be null if user has no main photo"
    }
  }
}
```

### 6.3 User Online (`user_online`)

Sent by the WS handler when a participant connects to the chat.

```json
{
  "type": "user_online",
  "user_id": "other-user-uuid"
}
```

### 6.4 User Offline (`user_offline`)

Sent by the WS handler when a participant disconnects.

```json
{
  "type": "user_offline",
  "user_id": "other-user-uuid"
}
```

### 6.5 Typing Indicator (`typing`)

Sent when the other user starts typing (published via WebSocketManager `set_typing()`, 5s TTL).

```json
{
  "type": "typing",
  "match_id": "match-uuid",
  "user_id": "other-user-uuid"
}
```

### 6.6 Typing Stopped (`typing_stopped`)

Sent when the other user stops typing, sends a message, or disconnects.

```json
{
  "type": "typing_stopped",
  "match_id": "match-uuid",
  "user_id": "other-user-uuid"
}
```

### 6.7 Read Receipts (`messages_read`)

Sent when the other user marks messages as read (via WS `read` command).

```json
{
  "type": "messages_read",
  "message_ids": ["uuid1", "uuid2"],
  "reader_id": "other-user-uuid"
}
```

### 6.8 Pong Response (`pong`)

Server responds to client's `ping`.

```json
{
  "type": "pong"
}
```

---

## 7. Message Envelope Format

Every WebSocket message from the server follows this structure:

```json
{
  "type": "<event_type>",
  "data": { ... }
}
```

- `type` — string, one of: `new_message`, `new_match`, `user_online`, `user_offline`, `typing`, `typing_stopped`, `messages_read`, `pong`
- `data` — object, varies by event type (see Section 6)

The `_target_user` field used internally by `WebSocketManager.send_to_match()` is **stripped** before delivery to the client. You will never see it in a received message.

---

## 8. Rate Limits

| Endpoint | Method | Limit |
|----------|--------|-------|
| `GET /messages/{identifier}` | GET | 60/min |
| `POST /messages/{identifier}/text` | POST | 60/min |
| `POST /messages/{identifier}/photo` | POST | 30/min |
| `POST /messages/{identifier}/voice` | POST | 30/min |
| `POST /messages/{identifier}/accept` | POST | 20/min |
| `POST /messages/delivered` | POST | 100/min |
| `POST /messages/read` | POST | 100/min |
| `DELETE /messages/{message_id}` | DELETE | 30/min |
| `POST /messages/{message_id}/forward` | POST | 30/min |
| `GET /messages/{message_id}/status` | GET | 60/min |

Additionally, `POST /messages/{identifier}/text` enforces a **per-match in-memory rate limit**: max **30 messages per minute per sender per chat**.

---

## 9. Real-Time Flow Examples

### 9.1 Sending a Text Message

```
Flutter App                    Backend                        WebSocket
───────────                    ───────                        ──────────
1. User taps "Send"
2. POST /messages/{id}/text ──►│
   {content: "Hello"}          │  ├─ Validates, encrypts, stores in DB
   ◄── 200 {id, sent_at}       │  ├─ Enqueues background WS push
                               │  │
                               │  └─ BackgroundTask ──► publish to ws:chat:{match_id}
                               │                                  │
3. Show message in UI   ◄──────┘                                  │
4. (simultaneously)       ◄────────────────────────────────────────┘
                           ws message: {"type":"new_message", "data":{...}}
```

### 9.2 Typing Indicator

```
Flutter A                       Flutter B
───────────                     ──────────
1. User starts typing
2. WS send: {"type":"typing"} ──►│
                                  │  └─ publish typing event to ws:chat:{match_id}
                                  │                                  │
3. User sees "typing..." ◄───────┘
4. User sends message
5. WS send: {"type":"typing_stopped"} ──►│
                                        │  └─ publish typing_stopped event
6. "typing..." disappears ◄─────────────┘
```

### 9.3 Read Receipts

```
Flutter B                       Flutter A
───────────                     ──────────
1. User opens chat
2. Post /messages/read ──────►│  (DB: marks messages is_read=true)
   {message_ids: [uuid1, uuid2]} │
   ◄── 200                      │
3. (simultaneously)             │
                                │  └─ WS publish messages_read event
4. Show "Read" checkmark ◄─────┘
   ws message: {"type":"messages_read", "message_ids":[...], "reader_id":"B"}
```

---

## 10. Flutter Implementation Guide

### 10.1 WebSocket Connection Helper

```dart
class ChatWebSocketService {
  RawWebSocket? _socket;
  final String matchId;
  final String jwtToken;
  final _messageController = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionStateController = StreamController<bool>.broadcast();

  ChatWebSocketService({required this.matchId, required this.jwtToken});

  Stream<Map<String, dynamic>> get messages => _messageController.stream;
  Stream<bool> get connectionState => _connectionStateController.stream;

  static const _heartbeatInterval = Duration(seconds: 30);
  static const _reconnectMaxAttempts = 6;
  static const _baseReconnectDelay = Duration(seconds: 1);

  int _retryCount = 0;
  Timer? _heartbeatTimer;
  Timer? _reconnectTimer;

  Future<void> connect() async {
    final uri = Uri.parse(
      'wss://api.bondi.ir/ws/chat/$matchId?token=$jwtToken',
    );

    try {
      _socket = await RawWebSocket.connect(uri);
      _retryCount = 0;
      _connectionStateController.add(true);

      // Start heartbeat
      _heartbeatTimer?.cancel();
      _heartbeatTimer = Timer.periodic(_heartbeatInterval, (_) {
        _send({'type': 'ping'});
      });

      // Listen for messages
      _socket!.listen(
        (data) {
          try {
            final parsed = json.decode(data as String) as Map<String, dynamic>;
            _handleServerMessage(parsed);
          } catch (_) {
            // Ignore malformed JSON
          }
        },
        onDone: _onDisconnected,
        onError: (_) => _onDisconnected(),
      );
    } catch (e) {
      _onDisconnected();
    }
  }

  void _handleServerMessage(Map<String, dynamic> msg) {
    final type = msg['type'] as String?;

    switch (type) {
      case 'pong':
        // Keepalive response, no action needed
        break;
      default:
        _messageController.add(msg);
        break;
    }
  }

  void _onDisconnected() {
    _heartbeatTimer?.cancel();
    _connectionStateController.add(false);
    _attemptReconnect();
  }

  void _attemptReconnect() {
    if (_retryCount >= _reconnectMaxAttempts) return;

    final delay = Duration(seconds: (_baseReconnectDelay.inSeconds * (1 << _retryCount)));
    // Cap at 30 seconds
    final cappedDelay = delay > const Duration(seconds: 30)
        ? const Duration(seconds: 30)
        : delay;

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(cappedDelay, () {
      _retryCount++;
      connect();
    });
  }

  void _send(Map<String, dynamic> data) {
    _socket?.add(json.encode(data));
  }

  void sendPing() => _send({'type': 'ping'});

  void sendTyping() => _send({'type': 'typing'});

  void sendTypingStopped() => _send({'type': 'typing_stopped'});

  void sendReadReceipt(List<Uuid> messageIds) {
    _send({
      'type': 'read',
      'message_ids': messageIds.map((id) => id.toString()).toList(),
    });
  }

  Future<void> dispose() async {
    _heartbeatTimer?.cancel();
    _reconnectTimer?.cancel();
    await _socket?.close();
    await _messageController.close();
    await _connectionStateController.close();
  }
}
```

### 10.2 Typing Detection with Debounce

```dart
class TypingIndicatorController {
  Timer? _typingTimer;
  bool _isTyping = false;

  void onTextChanged(ChatWebSocketService ws, String text) {
    if (!_isTyping) {
      _isTyping = true;
      ws.sendTyping();
    }

    _typingTimer?.cancel();
    _typingTimer = Timer(const Duration(seconds: 3), () {
      _isTyping = false;
      ws.sendTypingStopped();
      _typingTimer = null;
    });
  }

  void onMessageSent(ChatWebSocketService ws) {
    _typingTimer?.cancel();
    _typingTimer = null;
    _isTyping = false;
    ws.sendTypingStopped();
  }

  void dispose() {
    _typingTimer?.cancel();
  }
}
```

### 10.3 Send a Text Message (HTTP POST)

```dart
Future<SendMessageResponse> sendTextMessage(
  String matchId,
  String content,
  String authToken,
) async {
  final response = await http.post(
    Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/text'),
    headers: {
      'Authorization': 'Bearer $authToken',
      'Content-Type': 'application/json',
    },
    body: json.encode({'content': content}),
  );

  if (response.statusCode == 200) {
    return SendMessageResponse.fromJson(json.decode(response.body));
  }
  throw Exception('Failed to send message: ${response.statusCode}');
}
```

### 10.4 Send a Photo Message (HTTP POST multipart)

```dart
Future<SendMessageResponse> sendPhotoMessage(
  String matchId,
  String imagePath,
  String? caption,
  String authToken,
) async {
  final request = http.MultipartRequest(
    'POST',
    Uri.parse('https://api.bondi.ir/api/v1/messages/$matchId/photo'),
  );
  request.headers['Authorization'] = 'Bearer $authToken';
  request.fields['caption'] = caption ?? '';
  request.files.add(
    await http.MultipartFile.fromPath('file', imagePath),
  );

  final response = await request.send();
  final body = await response.stream.bytesToString();

  if (response.statusCode == 200) {
    return SendMessageResponse.fromJson(json.decode(body));
  }
  throw Exception('Failed to send photo: ${response.statusCode}');
}
```

### 10.5 Handling WebSocket Events in UI

```dart
class ChatScreen extends StatefulWidget {
  final String matchId;
  final String jwtToken;

  const ChatScreen({required this.matchId, required this.jwtToken, super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  late ChatWebSocketService _wsService;
  late TypingIndicatorController _typingController;
  List<Message> _messages = [];
  bool _isOtherUserOnline = false;
  bool _isTyping = false;

  @override
  void initState() {
    super.initState();
    _wsService = ChatWebSocketService(
      matchId: widget.matchId,
      jwtToken: widget.jwtToken,
    );
    _typingController = TypingIndicatorController();
    _wsService.connect();

    _wsService.messages.listen((event) {
      final type = event['type'] as String;
      final data = event['data'] as Map<String, dynamic>? ?? {};

      switch (type) {
        case 'new_message':
          _handleNewMessage(data);
          break;
        case 'typing':
          setState(() => _isTyping = true);
          break;
        case 'typing_stopped':
          setState(() => _isTyping = false);
          break;
        case 'user_online':
          setState(() => _isOtherUserOnline = true);
          break;
        case 'user_offline':
          setState(() => _isOtherUserOnline = false);
          break;
        case 'messages_read':
          _handleReadReceipts(data['message_ids'] as List);
          break;
      }
    });

    _wsService.connectionState.listen((connected) {
      if (!connected) {
        // Show offline indicator
      }
    });
  }

  void _handleNewMessage(Map<String, dynamic> data) {
    final message = Message.fromSocketData(data);
    setState(() {
      _messages.add(message);
    });

    // If this is a text message, mark as read via HTTP
    if (message.messageType == 'text') {
      _markMessagesRead([message.id]);
    }
  }

  Future<void> _markMessagesRead(List<Uuid> messageIds) async {
    await http.post(
      Uri.parse('https://api.bondi.ir/api/v1/messages/read'),
      headers: {
        'Authorization': 'Bearer ${widget.jwtToken}',
        'Content-Type': 'application/json',
      },
      body: json.encode({'message_ids': messageIds.map((id) => id.toString()).toList()}),
    );
  }

  void _handleReadReceipts(List dynamic messageIds) {
    setState(() {
      for (final idStr in messageIds) {
        final msg = _messages.firstWhere(
          (m) => m.id.toString() == idStr,
          orElse: () => Message.empty(),
        );
        if (msg.id != Uuid()) {
          msg.isRead = true;
        }
      }
    });
  }

  void _onSendButtonTap(String text) {
    _typingController.onMessageSent(_wsService);
    sendTextMessage(widget.matchId, text, widget.jwtToken);
    setState(() {
      _messages.add(Message.local(text, isMine: true));
    });
  }

  @override
  void dispose() {
    _typingController.dispose();
    _wsService.dispose();
    super.dispose();
  }
}
```

---

## 11. Error Handling

### WebSocket Close Codes

| Code | Reason | Action |
|------|--------|--------|
| `4001` | Unauthorized (invalid/expired JWT) | Redirect to login |
| `4003` | Access denied (not a match participant) | Show error, close chat |
| Other unexpected | Server error | Attempt reconnect with backoff |

### HTTP Error Codes for Message Endpoints

| Code | Meaning | Flutter Action |
|------|---------|----------------|
| 400 | Invalid input (missing fields, wrong format) | Show inline validation error |
| 401 | Unauthorized (expired token) | Refresh token, retry |
| 403 | Not a participant in this chat | Show "Chat not found" |
| 404 | Match/user not found | Show error |
| 429 | Rate limited | Show "Sending too fast" toast, wait |

---

## 12. Encryption Note

Messages are encrypted server-side using **AES-256-GCM** with keys derived per-match from `match_id + ENCRYPTION_SECRET` (PBKDF2, 100,000 iterations). The Flutter app **never needs to encrypt or decrypt** — it always receives plaintext content via the HTTP API (`GET /messages/{id}`) and the WebSocket push events. Encryption is transparent to the client.

---

## 13. Redis Keys Reference

Use these for debugging or monitoring:

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `online:{user_id}` | String | 60s | User is online if this key exists |
| `typing:{match_id}:{user_id}` | String | 5s | User is typing (auto-expires) |
| `ws:user:{user_id}` | Pub/Sub channel | — | Per-user event delivery |
| `ws:chat:{match_id}` | Pub/Sub channel | — | Per-match chat event delivery |

---

## 14. Message Type Summary Table

### What Flutter sends (WebSocket)

| Direction | `type` value | Client fields | Server action |
|-----------|-------------|---------------|---------------|
| Client → Server | `ping` | none | Reply with `pong`, refresh online TTL |
| Client → Server | `typing` | none | Publish typing event to match |
| Client → Server | `typing_stopped` | none | Clear typing indicator |
| Client → Server | `read` | `message_ids: list<uuid>` | Broadcast `messages_read` to other participant |

### What the server sends (WebSocket)

| `type` value | When | Key data fields |
|-------------|------|-----------------|
| `new_message` | Any message sent | `message_type`, `content`/`media_url`/`duration`, `sender_id`, `sent_at` |
| `new_match` | Match created | `match_id`, `user` (id, name, age, main_photo_url) |
| `user_online` | Other user connects to chat | `user_id` |
| `user_offline` | Other user disconnects | `user_id` |
| `typing` | Other user starts typing | `match_id`, `user_id` |
| `typing_stopped` | Other user stops typing | `match_id`, `user_id` |
| `messages_read` | Other user reads messages | `message_ids`, `reader_id` |
| `pong` | Response to client's ping | none |

---

# PART C — Development Environment & Testing

## Setup Guides (macOS / Windows)

### 🍎 macOS

```bash
# 1. Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install Python
brew install python@3.11

# 3. Clone the repository
git clone https://github.com/EhsanRezaie/dating-app.git
cd dating-app

# 4. Install uv
brew install uv

# 5. Create the virtualenv + install dependencies
uv sync --all-groups

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

# 4. Install uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 5. Create the virtualenv + install dependencies
uv sync --all-groups

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
- **Bugsink dashboard:** http://localhost:8080 (login `admin@bondi.local` / `admin123`) → error tracking dashboard

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

## Docker Services

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| **Nginx** | **80** | **Reverse proxy (entry point)** |
| **App (FastAPI)** | 8000 (internal) | API server |
| PostgreSQL + PostGIS | 5432 | Main database |
| Redis | 6379 | Cache + realtime |
| MinIO | 9000 (API), 9001 (console) | Photo storage (S3-compatible) |
| Bugsink | 8080 | Error tracking dashboard + event ingestion |

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

## Bugsink Error Tracking (Local Setup)

## Bugsink Error Tracking Setup

[Bugsink](https://www.bugsink.com/) is a self-hosted error tracker that is compatible with the Sentry SDK. It catches unhandled exceptions and errors from the FastAPI app and displays them in a web dashboard.

### How It Works

- **Bugsink** — a single Docker container that receives error events from the app via the Sentry SDK protocol, stores them, and serves the dashboard UI
- **sentry-sdk** — the Python client in `app/core/error_handling.py` auto-captures exceptions and sends them to Bugsink (Bugsink is Sentry-compatible, so no client changes are needed)
- **PostgreSQL** — Bugsink stores projects/events in the `bondi_bugsink` database on the existing `db` service (no Redis or worker container required)

### Architecture

```
FastAPI App ──sentry_sdk──▸ Bugsink (:8080) ──▸ PostgreSQL (bondi_bugsink)
```

### Prerequisites

No additional dependencies. Bugsink runs in Docker (included in `docker-compose.yml`). The Python `sentry-sdk[fastapi]` package is already in `pyproject.toml`.

### Platform Setup

Bugsink setup is the **same on all platforms** — it runs entirely in Docker. The only prerequisite is Docker + Docker Compose installed and running.

#### Linux (Ubuntu/Debian)

```bash
# Docker is already installed from the main setup guide.
# Bugsink starts automatically with the rest of the stack:

docker compose up -d

# Verify the Bugsink container is running:
docker ps --filter "name=bondi_bugsink"
# Should show:
#   bondi_bugsink   Up   (web dashboard + event ingestion)

# Wait ~15 seconds for migrations, then open:
# Dashboard: http://localhost:8080
```

#### macOS

```bash
# Same commands — Docker Desktop handles the Linux containers:

docker compose up -d

# Verify:
docker ps --filter "name=bondi_bugsink"

# Open: http://localhost:8080
```

#### Windows (WSL2 or PowerShell)

```powershell
# Same commands — Docker Desktop handles the Linux containers:

docker compose up -d

# Verify:
docker ps --filter "name=bondi_bugsink"

# Open: http://localhost:8080
```

#### Server Deployment (Linux VPS / Cloud)

```bash
# 1. Clone and start everything
git clone <repo-url> && cd project-d
docker compose up -d

# 2. Wait for Bugsink to initialize (~15 seconds)
sleep 15

# 3. The superuser is created automatically on first boot from BUGSINK_SUPERUSER
#    (set in .env as email:password). Log in at http://<server-ip>:8080

# 4. Create an organization + project in the dashboard (or via the Bugsink API),
#    then copy the generated DSN.

# 5. Update .env with the DSN (replace YOUR_SERVER_IP with your actual IP)
#    BUGSINK_DSN=http://<public_key>@YOUR_SERVER_IP:8080/1

# 6. Restart the app to pick up the new DSN
docker compose up -d app
```

### First-Time Bugsink Setup (All Platforms)

On first boot, Bugsink runs migrations and creates the superuser defined by
`BUGSINK_SUPERUSER` in `.env` (`email:password`). Then, in the dashboard:

1. Open `http://localhost:8080` and log in with `BUGSINK_SUPERUSER`.
2. Create an organization and a project.
3. Copy the project **DSN** (format `http://<public_key>@<host>:<port>/<project_id>`).
4. Set `BUGSINK_DSN` in `.env`, then `docker compose up -d app`.

### Configuration (.env)

```env
# Bugsink DSN — get this from the dashboard after first-time setup
# Format: http://<public_key>@<host>:<port>/<project_id>
BUGSINK_DSN=

# Bugsink secret key — generate with: openssl rand -hex 32 (must be >= 50 chars)
BUGSINK_SECRET_KEY=

# Base URL where Bugsink is reachable (used to build links and DSNs)
BUGSINK_BASE_URL=http://localhost:8080

# Host/domain names Bugsink accepts. "*" disables host validation.
BUGSINK_ALLOWED_HOSTS=*

# Set true when served over HTTPS by a reverse proxy (nginx).
BUGSINK_BEHIND_HTTPS_PROXY=false

# Superuser created on first boot, as email:password
BUGSINK_SUPERUSER=admin@bondi.local:admin123
```

### Verifying It Works

```bash
# 1. Start the FastAPI app
uvicorn app.main:app --reload

# 2. Open the Bugsink dashboard
#    http://localhost:8080
#    Login: BUGSINK_SUPERUSER from .env

# 3. Send a test error from a separate terminal:
uv run python -c "
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

The FastAPI app initializes `sentry_sdk` in `app/core/error_handling.py` on startup:

```python
if settings.BUGSINK_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=settings.BUGSINK_DSN,
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
| `bondi_bugsink` | `bugsink` | Web UI + event ingestion API (port 8080) |
| `bondi_bugsink_db_init` | `bugsink-db-init` | One-shot: creates the `bondi_bugsink` database |

### Troubleshooting

**Dashboard not loading:**
- Check the container is running: `docker ps --filter "name=bondi_bugsink"`
- Logs: `docker logs bondi_bugsink`

**Events not showing up in dashboard:**
- Confirm `BUGSINK_DSN` is set in `.env` and the app was restarted (`docker compose up -d app`)
- Trigger a test error (see *Verifying It Works*) and check the dashboard

**Bugsink won't start / database errors:**
- Ensure the `bondi_bugsink` database exists: `docker compose logs bugsink-db-init`
- Recreate from scratch: `docker compose down -v && docker compose up -d`
- Wait ~15 seconds for initialization

**`sentry_sdk` import error on app startup:**
- Install the missing dependency: `uv add sentry-sdk[fastapi]`
- If you see `jinja2 must be installed`: `uv add jinja2`

**Port 8080 already in use:**
- Change the mapping in `docker-compose.yml`:
  ```yaml
  bugsink:
    ports:
      - "8180:8000"   # use port 8180 instead
  ```
- Update `BUGSINK_BASE_URL` and `BUGSINK_DSN` in `.env` to use the new port

### Production / Server Deployment Notes

- Change the Bugsink superuser password immediately after first setup
- Set a strong `BUGSINK_SECRET_KEY` (generate with `openssl rand -hex 32`, must be at least 50 chars)
- In production the dashboard is served at `https://log.bondiapp.ir` (publicly reachable, gated by Bugsink's own login). Set `BUGSINK_BASE_URL=https://log.bondiapp.ir`, `BUGSINK_ALLOWED_HOSTS=log.bondiapp.ir,bugsink`, and `BUGSINK_BEHIND_HTTPS_PROXY=true`.
- In production point the app at the **internal** service, not the public URL: `BUGSINK_DSN=http://<public_key>@bugsink:8000/<project_id>`.
- Set `traces_sample_rate` to `0.0` in production to disable performance tracing (or keep `0.1` for 10% sampling)
- Bugsink retains events according to its retention settings (configurable)


## Seeding Reference Data

## Seeding Reference Data

Static reference tables (e.g. `interests`) are populated from JSON files under `app/db/seed_data/` using idempotent seed scripts in `app/db/scripts/`. Safe to re-run anytime — existing rows are updated in place, new rows are inserted, nothing is duplicated or deleted.

```bash
# Seed / update interests from app/db/seed_data/interests.json
python -m app.db.scripts.seed_interests

# Seed 1000 dummy users (test1@test.com … test1000@test.com, password: 12345678)
python -m app.db.scripts.seed_dummy_users
```

Run these after migrations on first setup. Interests can be re-run any time `interests.json` is edited. Dummy users can be re-run safely — existing `%@test.com` users are deleted first.

## Testing Strategy

## 12. Testing Strategy

### Test Files (all in `tests/`)

| Session | Test Files | Tests | Status |
|---------|------------|-------|--------|
| All | 33 test files in `tests/` | **627** | **✅ All passing** |
| 25 | test_auth, test_users, test_photos, test_prompts, test_settings, test_encryption | 101 | ✅ |
| 25 | test_swipes, test_matches, test_blocks, test_discover, test_search | 110 | ✅ |
| 25 | test_rewards, test_referrals, test_subscriptions, test_daily_limits | 79 | ✅ |
| 25 | test_notifications, test_reports, test_tickets | 95 | ✅ |
| 25 | test_admin_dashboard, test_admin_messages, test_admin_photos | 56 | ✅ |
| 25 | test_admin_reports, test_admin_tickets, test_admin_users | 70 | ✅ |
| 32 | test_websocket | 9 | ✅ |
| 34 | test_push_notifications | 9 | ✅ |
| 42+ | test_nsfw | 12 | ✅ |

### Run All Tests

```bash
uv run pytest tests/ -v
```

### Run a Single File

```bash
uv run pytest tests/test_messages_encryption.py -v
```

---

## Compose Files (Development & Testing)

### Docker Compose (Development)

```yaml
version: '3.9'

services:
  db:
    image: postgis/postgis:15-3.3
    container_name: bondi
    environment:
      POSTGRES_USER: bondi_admin
      POSTGRES_PASSWORD: CHANGE_ME
      POSTGRES_DB: bondi
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    container_name: bondi_redis
    ports:
      - "6379:6379"

  minio:
    image: ehsanpc/bondi-minio:RELEASE.2025-09-07T16-13-09Z
    container_name: bondi_minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"   # S3 API
      - "9001:9001"   # Web console
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 5

  minio-init:
    image: ehsanpc/bondi-minio-mc:RELEASE.2025-09-07T16-13-09Z
    container_name: bondi_minio_init
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 minioadmin minioadmin &&
      mc mb --ignore-existing local/photos-public &&
      mc mb --ignore-existing local/photos-private &&
      mc anonymous set download local/photos-public &&
      echo 'MinIO buckets ready'
      "

volumes:
  postgres_data:
  minio_data:
```

### Docker Compose (Testing)

```yaml
version: '3.9'

services:
  db_test:
    image: postgis/postgis:15-3.3
    container_name: bondi_test
    environment:
      POSTGRES_USER: bondi_admin
      POSTGRES_PASSWORD: CHANGE_ME
      POSTGRES_DB: bondi_test
    ports:
      - "5433:5432"
    volumes:
      - postgres_test_data:/var/lib/postgresql/data

  redis_test:
    image: redis:7-alpine
    container_name: bondi_redis_test
    ports:
      - "6380:6379"

  minio-test:
    image: ehsanpc/bondi-minio:RELEASE.2025-09-07T16-13-09Z
    container_name: bondi_minio_test
    command: server /data --console-address ":9091"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9090:9000"   # S3 API
      - "9091:9091"   # Web console
    volumes:
      - minio_test_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 5

  minio-test-init:
    image: ehsanpc/bondi-minio-mc:RELEASE.2025-09-07T16-13-09Z
    container_name: bondi_minio_test_init
    depends_on:
      minio-test:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio-test:9000 minioadmin minioadmin &&
      mc mb --ignore-existing local/photos-public-test &&
      mc mb --ignore-existing local/photos-private-test &&
      mc anonymous set download local/photos-public-test &&
      echo 'Test MinIO buckets ready'
      "

volumes:
  postgres_test_data:
  minio_test_data:
```

### Alembic Commands

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

---

# PART D — Current Hardening State

## Scale — Where You Stand

## Where You Stand

| Area | Status |
|------|--------|
| DB indexes (37 across 10 tables) | ✅ Done |
| Redis caching (static + user + daily limits) | ✅ Done |
| Haversine in PostgreSQL | ✅ Done |
| Eager loading / N+1 eliminated | ✅ Done |
| Cursor pagination for chat | ✅ Done |
| BackgroundTasks for notifications | ✅ Done |
| GZip middleware | ✅ Done |
| 556 tests passing | ✅ Done |
| FCM push notifications | ✅ Done (Session 34) |
| Structured logging + Bugsink | ✅ Done (Session 33) |
| Auth hardening (token, enumeration, OTP) | ✅ Done (Session 35) |
| Location fuzzing | ✅ Done (Session 37) |
| Per-match message rate limit | ✅ Done (Session 38) |
| Daily report limit | ✅ Done (Session 39) |
| CORS fix | ✅ Done (Session 40) |
| Swipe deduplication (Redis set) | ✅ Done (Session 41) |
| Discover card stack caching | ✅ Done (Session 41) |
| Real ZarinPal payment | ❌ Missing |
| Email sending (verification, reset) | ❌ Missing |
| Connection pooling (PgBouncer) | ❌ Missing |
| Rate limiting per endpoint | ✅ Done |
| WebSocket scale (multi-process) | ❌ Missing |
| Presence (who's online) | ❌ Missing |
| Typing indicators | ❌ Missing |
| Health checks for Docker | ❌ Missing |
| Nginx reverse proxy | ❌ Missing |
| HTTPS / SSL | ❌ Missing |
| Discover card stack pre-caching | ✅ Done (Session 41) |
| Swipe deduplication (Redis set) | ✅ Done (Session 41) |
| Photo moderation pipeline | ❌ Partial |

---

## Security Inventory

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
| Audit log | ✅ | `admin_logs` table + `log_admin_action()` (verify coverage: TODO P3-4) |
| Token theft detection | ✅ | Refresh-token family tracking (TODO P3-3) |

---


## Infrastructure Checklist (implemented) — stale-checklist reconciliation

The following items are **unchecked in the old plans but already implemented** in code.
Listed so you don't waste a session re-doing them. The priority references (P0–P6)
point at tasks in [`TODO.md`](TODO.md).

- ✅ WebSocket rewrite / Redis Pub/Sub multi-worker — `websocket_manager.py` (scale §3.4, Session B)
- ✅ Presence (who's online) — `websocket_manager.py:246-261`
- ✅ Typing indicators — `websocket_manager.py:263-292`
- ✅ `validate_ws_token` in deps — `deps.py:154-167`
- ✅ `is_online` in match/search/discover responses — `matches.py`, `discover.py:237`, `search.py:324`
- ✅ Discover card stack pre-caching — `cache.py:81-122`
- ✅ Swipe deduplication (Redis set) — `cache.py:126-143`
- ✅ All `broadcast_match`/`send_to_match` callers pass `redis` — `swipes.py:79`, `messages.py:77`
- ✅ structlog JSON logging — `core/logging.py` (scale §7, Session D)
- ✅ Sentry/Bugsink SDK — `main.py:55-67` (scale §7, Session D) — but see P4-1 (DSN)
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

