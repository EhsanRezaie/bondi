```markdown
# dev.md — Iranian Dating App (Badoo-style)

> **Purpose:** Single source of truth for the entire project.  
> Updated at the end of every session. Pass this file to Claude at the start of every new session.  
> Claude must read this file fully before taking any action.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Team & Timeline](#2-team--timeline)
3. [Tech Stack](#3-tech-stack)
4. [Repository Structure](#4-repository-structure)
5. [Environment & Configuration](#5-environment--configuration)
6. [Database Schema](#6-database-schema)
7. [API Reference](#7-api-reference)
8. [Architecture Decisions](#8-architecture-decisions)
9. [Business Rules](#9-business-rules)
10. [Session Progress](#10-session-progress)
11. [Session 15 Plan: Push Notifications + Production Ready](#11-session-15-plan-push-notifications--production-ready)
12. [Testing Strategy](#12-testing-strategy)
13. [Deployment Notes](#13-deployment-notes)

---

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
│   │   │   │   └── dummy_users.json           # 1000 users for local dev
│   │   │   └── scripts/
│   │   │       ├── seed_interests.py          # Idempotent upsert seed/sync script
│   │   │       └── seed_dummy_users.py        # Idempotent seeder (python -m app.db.scripts.seed_dummy_users)
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
│   ├── docker-compose.yml                 # App + all infrastructure (db, redis, minio, nginx, bondi_glitchtip)
│   ├── docker-compose.test.yml            # Test infrastructure (db_test, redis_test, minio-test)
│   ├── requirements.txt
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
| `/search` | GET | Supports pagination with `limit` and `offset` |
| `/search` | GET | Supports sorting by recent, distance, age, name |

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

## 10. Session Progress

| Session | Focus | Status |
|---------|-------|--------|
| 1-2 | Project setup, Docker, base models | ✅ |
| 3 | Auth endpoints | ✅ |
| 4 | Auth hardening | ✅ |
| 5 | Users endpoints | ✅ |
| 6 | Photo upload + admin moderation | ✅ |
| 7 | Discover + Swipe system | ✅ |
| 8 | Search + Block system | ✅ |
| 9 | Match list + WebSocket | ✅ |
| 10 | Chat system | ✅ |
| 11 | Premium + Daily Limits + Ad Rewards + Referrals | ✅ |
| 12 | Notifications + Privacy + Reports | ✅ |
| 13 | Admin Panel (Tickets + Reports + User management + Dashboard + Announcements) | ✅ |
| 14 | Location fields + Referral complete + Reverse geocoding + Search by country | ✅ |
| 15 | Push notifications + Real Payment + Production | 🔲 |
| 16-17 | Flutter mobile app - Auth screens (Splash, Login, Sign Up, Verify) | ✅ |
| 18 | Flutter - Token persistence + Backend compatibility fixes | ✅ |
| 19 | Flutter - Onboarding Flow (Lifestyle, Interests, Location) | ✅ |
| 20 | Flutter - Main App Features (Discover, Search, Chats, Profile) | 🔲 |
| 21 | **Flutter - Profile Edit & Account Settings** | ✅ |
| 22 | **Message Encryption (AES-256-GCM)** | ✅ |
| 23 | **Discover & Search Updates, Interests Endpoint, Test Coverage** | ✅ |
| 24 | **System Status & Version Check API** | ✅ |
| 25 | **Test migration + backend User.profile fixes (511 tests)** | ✅ |
| 26 | **Dummy user seeder (1000 users)** | ✅ |
| 27 | **Performance Phase 1 — indexes, GZip, Cache-Control, limit caps** | ✅ |
| 28 | **Performance Phase 2+3 — Redis caching (static + user data + daily limits)** | ✅ |
| 29 | **Performance Phase 4.1 — get_current_user_id lightweight dependency** | ✅ |
| 30 | **Performance Phase 4.2-4.5 — Eager loading, DB Haversine, BackgroundTasks, Cursor pagination** | ✅ |
| 31 | **Schema audit + Redoc accuracy — all endpoints now declare response_model** | ✅ |
| 32 | **WebSocket tests — push shape validation + manager unit tests** | ✅ |
| 33 | **Structured logging + GlitchTip error tracking** | ✅ |
| 34 | **Push notifications (FCM) + Device tokens + messages fix** | ✅ |
| 35 | **Auth hardening — token expiry, enumeration fix, OTP brute-force, Swagger lockdown** | ✅ |
| 36 | **IDOR audit + EXIF stripping + dead code cleanup** | ✅ |
| 37 | **Location fuzzing — ±500m noise on discover/search distance** | ✅ |
| 38 | **Per-match message rate limit — 30/min per sender per chat** | ✅ |
| 39 | **Daily report limit — 5 reports/day per user** | ✅ |
| 40 | **CORS fix — configurable origins via CORS_ORIGINS env var** | ✅ |
| 41 | **Redis perf — swipe deduplication + discover card stack cache** | ✅ |
| 42 | **Security hardening — constant-time login, IP tracking, admin JWT + audit log** | ✅ |
| 42+ | **NSFW photo moderation — skin-tone heuristic, quarantine, 12 tests** | ✅ |
| 44 | **DailyLimit race condition fix + SQL logging cleanup** | ✅ |
| 45 | **DailyLimit upsert fix — `on_conflict_do_nothing` replaces broken `on_conflict_do_update(set_={})`** | ✅ |
| 46 | **Discover distance fix — `distance_km` now optional (default None), no filter when omitted** | ✅ |
| 47 | **Race condition audit — photos FOR UPDATE, double-commit fix, Redis pipeline incr+expire** | ✅ |
| 48 | **Search API parity — search now returns same fields as discover** | ✅ |
| 49 | **Swipe + message responses return live likes/chats remaining counts** | ✅ |
| 50 | **WebSocket presence + typing indicators + Redis Pub/Sub** | ✅ |
| 51 | **Docker health checks + dynamic multi-worker** | ✅ |
| 52 | **Search `sort_by=last_seen` + Redis real-time `is_online` in search/discover** | ✅ |
| 53 | **Swipes liked/likers endpoints — GET /swipes/liked & GET /swipes/likers with pagination** | ✅ |

---

## 11. Session 15 Plan: Push Notifications + Production Ready

### Goal
Real push notifications via Firebase Cloud Messaging, real ZarinPal integration, performance optimization, and production readiness.

### Tasks

#### 1. Push Notifications (FCM) ✅ DONE

**Files Created:**

| File | Purpose |
|------|---------|
| `app/services/push_service.py` | FCM send_push(), send_to_topic() |
| `app/models/device_token.py` | Store FCM tokens per user/device |

**Files Updated:**

| File | Changes |
|------|---------|
| `app/services/notification_service.py` | Call push_service after creating DB notification |
| `app/api/v1/endpoints/notifications.py` | Add POST /device-token endpoint |

#### 2. Real Payment Integration (ZarinPal)

**Files to Update:**

| File | Changes |
|------|---------|
| `app/api/v1/endpoints/subscriptions.py` | Replace mock with real ZarinPal calls |
| `app/services/payment_service.py` | NEW - real ZarinPal API integration |

**ZarinPal Flow:**
1. User selects plan → POST /subscriptions/purchase
2. Backend calls ZarinPal API → gets redirect URL
3. User pays on ZarinPal
4. ZarinPal redirects to /subscriptions/verify
5. Backend verifies payment → activates premium

#### 3. Performance Optimization

**Indexes to Add:**

```sql
CREATE INDEX idx_users_premium_until ON users(premium_until);
CREATE INDEX idx_users_province ON users(province);
CREATE INDEX idx_users_city ON users(city);
CREATE INDEX idx_notifications_user ON notifications(user_id, is_read, created_at DESC);
CREATE INDEX idx_messages_match ON messages(match_id, created_at DESC);
```

---

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
pytest tests/ -v
```

### Run a Single File

```bash
pytest tests/test_messages_encryption.py -v
```

---

## 13. Deployment Notes

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
    image: minio/minio:latest
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
    image: minio/mc:latest
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
    image: minio/minio:latest
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
    image: minio/mc:latest
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

## Session 21-26 Completion Summary

### ✅ Session 21 Complete - Flutter Profile Edit & Account Settings

| Feature | Status |
|---------|--------|
| ProfileScreen with 6 Account menu items | ✅ |
| Verify Picture status display | ✅ |
| EditBasicInfoScreen with location search | ✅ |
| EditProfileDetailsScreen with chip selection | ✅ |
| EditInterestsScreen with category grouping | ✅ |
| EditPromptsScreen with prompt answers | ✅ |
| Remove logout button from ProfileScreen | ✅ |
| UpdateProfile with null value handling | ✅ |
| updateInterests and updatePrompts API methods | ✅ |
| Backend PUT /users/me/interests endpoint | ✅ |
| Backend PUT /users/me/prompts endpoint | ✅ |
| User model with interests and promptsData | ✅ |
| mounted checks for async operations | ✅ |
| Enum mapping for profile details | ✅ |

### ✅ Session 22 Complete - Message Encryption

| Feature | Status |
|---------|--------|
| AES-256-GCM server-side encryption | ✅ |
| Per-chat encryption keys from match_id + ENCRYPTION_SECRET | ✅ |
| SQLAlchemy model property for auto encrypt/decrypt | ✅ |
| Admin decryption endpoints | ✅ |
| Photo/voice caption encryption | ✅ |
| `test_messages_encryption.py` (14 tests passing) | ✅ |
| `test_messages.py` (19 tests passing) | ✅ |
| ENCRYPTION_SECRET in .env | ✅ |
| Chat media settings in config | ✅ |

### ✅ Session 23 Complete - Discover, Search, Interests & Tests

| Feature | Status |
|---------|--------|
| Discover endpoint - gender filter (optional) | ✅ |
| Discover endpoint - block exclusions (both directions) | ✅ |
| Discover endpoint - matched user exclusions | ✅ |
| Discover endpoint - uses profile.age property | ✅ |
| Search endpoint - uses profile.age property | ✅ |
| Search endpoint - block exclusions (both directions) | ✅ |
| Interests endpoint - public, no auth | ✅ |
| Interests endpoint - returns 158 interests sorted | ✅ |
| `test_blocks.py` (12 tests passing) | ✅ |
| `test_search.py` (38 tests passing) | ✅ |
| `test_discover.py` (23 tests passing) | ✅ |
| `test_interests.py` (21 tests passing) | ✅ |
| `conftest.py` - auto-seeds interests | ✅ |
| `conftest.py` - re-seeds interests after each test | ✅ |

### ✅ Session 24 Complete - System Status & Version Check API

| Feature | Status |
|---------|--------|
| `/system/status` - System health check | ✅ |
| `/system/version-check` - App version compatibility | ✅ |
| Maintenance mode with admin control | ✅ |
| Force update capability | ✅ |
| Runtime version overrides | ✅ |
| `app/schemas/system.py` - System schemas | ✅ |
| `app/api/v1/endpoints/system.py` - System endpoints | ✅ |
| `tests/test_system.py` (24 tests passing) | ✅ |
| `APP_VERSION`, `MIN_ANDROID_VERSION`, `MIN_IOS_VERSION` in .env | ✅ |
| `version_override.json` for runtime overrides | ✅ |
| `maintenance.json` for maintenance mode | ✅ |

### ✅ Session 25 Complete - Test Migration & Backend Fixes

| Feature | Status |
|---------|--------|
| Migrate all 29 test files to `tests/` | ✅ |
| Fix `User.profile` access across 10+ endpoints (add `selectinload`) | ✅ |
| Convert all tests to 3-step registration flow | ✅ |
| Fix `user.premium_until` → `user.profile.premium_until` in subscriptions | ✅ |
| Fix `user.name`/`user.gender` → `user.profile.name`/`user.profile.gender` | ✅ |
| Add `test_settings.py` (169 tests) | ✅ |
| Add `test_swipes.py` (321 tests) | ✅ |
| Rewrite `test_matches.py`, `test_referrals.py`, `test_rewards.py`, `test_subscriptions.py` | ✅ |
| Rewrite `test_notifications.py`, `test_reports.py`, `test_tickets.py` | ✅ |
| Rewrite `test_admin_*.py` (6 files) | ✅ |
| **Total: 511 tests passing** | **✅** |

### ✅ Session 26 Complete - Dummy User Seeder

| Feature | Status |
|---------|--------|
| `app/db/seed_data/dummy_users.json` — 1000 users with full profiles | ✅ |
| `app/db/scripts/seed_dummy_users.py` — Idempotent seeder | ✅ |
| Password `12345678` for all dummy accounts | ✅ |
| `test1@test.com` … `test1000@test.com` naming | ✅ |
| `python -m app.db.scripts.seed_dummy_users` command | ✅ |
| README.md updated with seed command | ✅ |
| `ALTER TABLE photos ADD COLUMN crop JSON` applied to dev DB | ✅ |

### ✅ Session 27 Complete - Performance Phase 1

| Feature | Status |
|---------|--------|
| DB indexes in models (`__table_args__` + `Index`) — 37 across 10 tables | ✅ |
| Alembic migration `7f10ad4c02b9` applied to dev + test DBs | ✅ |
| `EXPLAIN ANALYZE` — all `Index Scan`, zero `Seq Scan` | ✅ |
| GZip middleware in `app/main.py` (≥1KB responses auto-compressed) | ✅ |
| `Cache-Control` headers on 5 public endpoints (interests, prompts, locations/countries, plans, status) | ✅ |
| `limit` cap `le=50` enforced on 6 list endpoints (discover, search, matches, messages, notifications, blocks) | ✅ |

---

### ⚠️ Pending

| Item | Priority | Session |
|------|----------|---------|
| Edit Photos Screen (photo management) | High | 21 |
| Face verification UI | Medium | 21 |
| Persian translations for all screens | High | — |
| Real ZarinPal integration | High | 15 |
| Real face-match API (photo verification) | Medium | ✅ Session 43 |
| Flutter Discover Screen | High | 20 |
| Flutter Search Screen | High | 20 |
| Flutter Chat System | High | 20 |

---

### ✅ Session 28 Complete - Performance Phase 3 — User Cache + Daily Limits

| Feature | Status |
|---------|--------|
| `GET /api/v1/users/me` cached per user (10min TTL, `model_dump`/`model_validate`) | ✅ |
| `invalidate_user_cache()` called on all 11 mutation endpoints (users, photos, locations) | ✅ |
| Daily limits cached in Redis (midnight TTL) via `reward_service.get_or_create_daily_limit` | ✅ |
| Cache updated after every `consume_like`, `consume_chat`, `claim_ad_reward` | ✅ |
| **All 511 tests passing** | ✅ |

**Mutation endpoints with cache invalidation:**
- `PUT /users/me`, `PUT /users/me/settings`, `DELETE /users/me`
- `POST /users/me/location`, `PATCH /users/me/location-text`
- `PATCH /locations/me/location-gps`, `PATCH /locations/me/location-manual`
- `POST /users/me/photos`, `DELETE /users/me/photos/{id}`
- `PUT /users/me/photos/{id}/main`, `PATCH /users/me/photos/{id}/crop`
- `PUT /users/me/interests`, `PUT /users/me/prompts`

**Files Modified:**
- `app/api/v1/endpoints/users.py` — cache GET /users/me, invalidate in all mutations
- `app/api/v1/endpoints/photos.py` — invalidate in upload/delete/main/crop
- `app/api/v1/endpoints/locations.py` — invalidate in GPS/manual location
- `app/services/reward_service.py` — Redis cache for daily limits (get_or_create + post-mutation sync)

---

### ✅ Session 30 Complete — Performance Phase 4.2-4.5 (Backend Query Optimization)

**Phase 4.2 — Eager Loading:**
- `GET /discover`: Removed redundant `current_user` query, dropped unused `UserSettings` load, `selectinload(User.photos)` only
- `GET /search`: Same + removed redundant `current_user` query
- `GET /matches`: Single `DISTINCT ON (match_id)` query replaces N+1 per-match last-message queries

**Phase 4.3 — DB-Level Haversine Distance:**
- `GET /discover`: Distance filter pushed to PostgreSQL `WHERE` clause (before `LIMIT`, so pagination is accurate)
- `GET /search`: Same + sort by distance moved to SQL `ORDER BY`

**Phase 4.4 — BackgroundTasks:**
- `POST /swipes`: Match notifications + photo URL queries + WebSocket broadcast moved to `BackgroundTasks`
- `POST /messages/{identifier}/text`: WebSocket notification backgrounded
- `POST /messages/{identifier}/photo`: WebSocket notification backgrounded
- `POST /messages/{identifier}/voice`: WebSocket notification backgrounded
- All use request session (stays open until after background tasks complete, `get_session` commits at cleanup)

**Phase 4.5 — Cursor Pagination:**
- `GET /messages/{identifier}`: Added `before` cursor param (ISO datetime)
- When provided: `Message.sent_at < before` replaces `OFFSET` — no expensive row-skipping
- `offset` kept as backward-compatible fallback
- Client passes `sent_at` of oldest loaded message as next cursor

**Files Modified:**
- `app/api/v1/endpoints/discover.py` — DB Haversine, redundant query removed, photo selectinload
- `app/api/v1/endpoints/search.py` — DB Haversine, SQL sort/pagination, redundant query removed
- `app/api/v1/endpoints/matches.py` — last-message N+1 eliminated, photo eager load
- `app/api/v1/endpoints/swipes.py` — BackgroundTasks for match notification + WebSocket
- `app/api/v1/endpoints/messages.py` — Cursor pagination, BackgroundTasks for WebSocket sends

**Tests: 511 passing ✅**

---

### ✅ Session 31 Complete — Schema Audit & Redoc Accuracy

Every endpoint in the app now declares a proper `response_model`, so Redoc shows accurate schemas for the Flutter client.

**Bug Fix:**
| File | Change |
|------|--------|
| `blocks.py:132` | `current_user.profile.name` → `user.profile.name` (was returning blocker's name, not blocked user's name) |
| `blocks.py:118` | Added `selectinload(User.profile)` to prevent `MissingGreenlet` on async lazy load |

**Endpoints wired with existing schemas (6):**

| Endpoint | Schema |
|----------|--------|
| `GET /swipes/stats` | `SwipeStatsResponse` |
| `POST /rewards/ad-watched` | `AdRewardResponse` |
| `GET /rewards/my-limits` | `DailyLimitsResponse` |
| `GET /referrals/my-code` | `ReferralCodeResponse` |
| `POST /referrals/claim` | `ClaimReferralResponse` |
| `GET /referrals/stats` | `ReferralStatsResponse` |

**New schema models created (12):**

| File | Models |
|------|--------|
| `location.py` | `LocationUpdateResponse` |
| `message.py` | `MessageActionResponse`, `ForwardMessageResponse` |
| `admin.py` | `AdminPendingPhotoResponse`, `AdminPhotoActionResponse`, `AdminPhotoRejectResponse`, `AdminPhotoVerifyResponse`, `AdminPhotoStatsResponse`, `AdminUserPhotoResponse`, `AdminMessageDecryptResponse`, `AdminMessageDeleteResponse`, `AdminReportedMessageResponse`, `UserActivityEntry` |
| `swipe.py` | Updated `SwipeStatsResponse` to match actual endpoint return; removed 5 dead models |

**Admin endpoints wired (11):**
- `GET /admin/photos/pending`, `POST /approve`, `POST /reject`, `GET /stats`, `GET /{id}`, `POST /verify-face`, `GET /users/{uid}/photos`
- `GET /admin/messages/{id}/decrypt`, `DELETE /admin/messages/{id}`, `GET /admin/messages/reports/{id}/message`
- `GET /admin/users/{uid}/activity`

**Other endpoints wired (6):**
- `PATCH /locations/me/location-gps` and `location-manual`
- `POST /messages/delivered`, `/read`, `DELETE /messages/{id}`, `POST /messages/{id}/forward`

**Dead code removed:**
- `swipe.py`: Deleted unused `SwipeResponse`, `SwipeHistoryResponse`, `SwipeListResponse`, `SwipeDirection`, old `SwipeRequest`

**AdminPhotoDetailResponse** — added missing `user_email` field.

**Tests: 511 passing ✅**

---

### ✅ Session 32 Complete — WebSocket Tests (Push Shape Validation + Manager Unit Tests)

**9 new tests in `tests/test_websocket.py`:**

| Test | What it validates |
|------|-------------------|
| `test_new_match_push_shape` | `broadcast_match` receives `{id, name, age, main_photo_url}` for both users |
| `test_text_message_push_shape` | `send_to_match` receives `{"type":"new_message","data":{id,message_type,content,sender_id,sent_at}}` |
| `test_photo_message_push_shape` | Same with `media_url`, `caption` — no `duration` |
| `test_voice_message_push_shape` | Same with `media_url`, `duration` — no `caption`/`content` |
| `test_broadcast_match_envelope` | Direct `WebSocketManager` unit test — JSON envelope structure (`type` + `data` nesting) |
| `test_send_to_match_envelope` | Same for `send_to_match` |
| `test_send_personal_message_envelope` | Same for `send_personal_message` |
| `test_disconnect_cleans_up` | Manager removes connection on disconnect |
| `test_send_personal_message_no_connection` | Manager doesn't raise on missing connection |

**Bug fix:**
| File | Change |
|------|--------|
| `chat_service.py:385-390` | `datetime.utcnow()` → `datetime.now(timezone.utc)` — fixed "can't compare offset-naive and offset-aware datetimes" crash in `delete_for="everyone"` branch |

**Tests: 556 passing ✅** (was 547)

---

### ✅ Session 33 Complete — Structured Logging (structlog + JSON)

| Feature | Status |
|---------|--------|
| `app/core/logging.py` rewritten with `structlog` + `JSONRenderer` (ISO timestamps, log level, logger name) | ✅ |
| File handler (`logs/app.log`) removed — JSON to stdout only | ✅ |
| `get_logger(name)` interface kept identical — all callers unchanged | ✅ |
| `GLITCHTIP_DSN=` added to `.env.example` (empty, fill in production) | ✅ |
| `sentry-sdk[fastapi]` added to `requirements.txt` | ✅ |
| `bondi_glitchtip` service + init added to `docker-compose.yml` (reuses existing Postgres + Redis) | ✅ |
| `bondi_glitchtip-test` service + init added to `docker-compose.test.yml` (port 8081, separate DB) | ✅ |
| `logger.exception()` added to `app/db/session.py` before rollback + `raise` | ✅ |
| Logger declarations added to **35 files**: 25 endpoints, 3 services, 6 core, 1 db | ✅ |
| **42 existing log calls** converted to structured key=value format across 9 files | ✅ |
| `structlog==26.1.0` added to `requirements.txt` | ✅ |
| All 547 tests still passing | ✅ |

**Running GlitchTip (dev):**
```bash
docker compose up -d bondi_glitchtip
```
Opens at `http://localhost:8080` — create account → create project → get DSN.

**Running GlitchTip (test):**
```bash
docker compose -f docker-compose.test.yml up -d bondi_glitchtip-test
```
Opens at `http://localhost:8081` — separate database and Redis namespace.

---

**Phase 5 — Flutter App Performance**
- [ ] `dio_cache_interceptor` + Hive store
- [ ] Per-endpoint cache policies
- [ ] `CachedNetworkImage` size limits
- [ ] Replace `Consumer` → `Selector` in hot paths
- [ ] `ListView.builder` + `RepaintBoundary` audit
- [ ] Parallelize splash screen
- [ ] WebSocket exponential backoff
- [ ] Notifications pagination

**Then: Session 15 — Push Notifications + Real Payment + Production Ready (Backend)**
```

### ✅ Session 34 Complete — Push Notifications (FCM) + Device Tokens + Messages Fix

| Feature | Status |
|---------|--------|
| `app/services/push_service.py` — FCM MulticastMessage send + token cleanup | ✅ |
| `app/models/device_token.py` — device_tokens table (user_id, token, platform) | ✅ |
| `NotificationService.notify_like()` — sends push to liked user | ✅ |
| `NotificationService.notify_match()` — sends push to both matched users | ✅ |
| `NotificationService.notify_message()` — sends push to message receiver | ✅ |
| POST `/notifications/device-token` — register/update FCM token | ✅ |
| DELETE `/notifications/device-token/{id}` — remove device token | ✅ |
| `messages.py` — added `notify_message()` call after text message creation | ✅ |
| `User.device_tokens` relationship | ✅ |
| `FCM_SERVICE_ACCOUNT_PATH` in config + .env | ✅ |
| `firebase-admin==6.8.0` dependency | ✅ |
| `tests/test_push_notifications.py` — 9 tests (6 device token + 3 push) | ✅ |
| **Total: 556 tests passing** | **✅** |

### ✅ Session 35 Complete — Auth Hardening

| Feature | Status |
|---------|--------|
| `ACCESS_TOKEN_EXPIRE_MINUTES` reduced from 7 days → 15 minutes | ✅ |
| `register/init` returns same response for existing emails (enumeration protection) | ✅ |
| OTP brute-force protection: max 5 attempts per code (register/verify) | ✅ |
| OTP brute-force protection: max 5 attempts per code (password-reset/verify) | ✅ |
| `verify_code_with_attempts()` with attempt counter in Redis (JSON format) | ✅ |
| Backward-compatible with plain-string codes (test fixtures) | ✅ |
| Swagger/Redoc/OpenAPI disabled when `ENVIRONMENT != "development"` | ✅ |
| `.env.example` updated with new token expiry | ✅ |
| Auth tests updated for new behavior (24/24 passing) | ✅ |

### ✅ Session 36 Complete — IDOR Audit + EXIF Stripping

**IDOR audit results (all already solid):**
| Endpoint | Check | Status |
|----------|-------|--------|
| `DELETE /photos/{id}` | `WHERE id AND user_id` | ✅ |
| `PUT /photos/{id}/main` | `WHERE id AND user_id` | ✅ |
| `PATCH /photos/{id}/crop` | `WHERE id AND user_id` | ✅ |
| `DELETE /notifications/{id}` | `WHERE id AND user_id` | ✅ |
| `POST /notifications/read` | `WHERE user_id` in UPDATE | ✅ |
| `GET /tickets/{id}` | `WHERE id AND user_id` | ✅ |
| `DELETE /messages/{id}` | `sender_id == user_id OR receiver_id == user_id` | ✅ |

**Fixes applied:**
| Feature | Status |
|---------|--------|
| EXIF metadata stripped from uploaded photos (prevents GPS/device leakage) | ✅ |
| Rate limiter added to `reorder_photos` (10/minute) | ✅ |
| Dead `select` query removed from `mark_notifications_read` | ✅ |
| Pillow `getdata()` deprecation warning fixed | ✅ |

### ✅ Session 37 Complete — Location Fuzzing

| Feature | Status |
|---------|--------|
| `app/utils/geo.py` — `fuzz_distance()` adds ±500m noise | ✅ |
| Applied to `GET /discover` distance_km responses | ✅ |
| Applied to `GET /search` distance_km responses | ✅ |
| DB coordinates remain exact (Haversine filter unaffected) | ✅ |

### ✅ Session 38 Complete — Per-Match Message Rate Limit

| Feature | Status |
|---------|--------|
| Redis counter `msg_rate:{sender_id}:{chat_id}`, 30/min | ✅ |
| 60s TTL window per sender per chat | ✅ |
| Graceful fallback if Redis unavailable | ✅ |

### ✅ Session 39 Complete — Daily Report Limit

| Feature | Status |
|---------|--------|
| Redis counter `reports:{user_id}:{date}`, 5/day | ✅ |
| 24h TTL window per reporter per day | ✅ |
| Graceful fallback if Redis unavailable | ✅ |

### ✅ Session 40 Complete — CORS Fix

| Feature | Status |
|---------|--------|
| `CORS_ORIGINS` setting in config.py | ✅ |
| Configurable origins via `.env` | ✅ |
| Default: wildcard with credentials disabled (mobile-only) | ✅ |
| Restricted methods and headers | ✅ |

### ✅ Session 41 Complete — Redis Performance (Swipe Dedup + Discover Cache)

| Feature | Status |
|---------|--------|
| `swiped:{user_id}` Redis set (7-day TTL) for fast exclusion | ✅ |
| Discover endpoint uses Redis set for swiped user exclusion | ✅ |
| DB subquery fallback when Redis set is empty | ✅ |
| `record_swipe_cache()` called on every swipe action | ✅ |
| Discover card stack cache functions (pop/set/invalidate) | ✅ |
| `app/core/cache.py` — all cache helpers centralized | ✅ |

### ✅ Session 42 Complete — Security Hardening

| Feature | Status |
|---------|--------|
| Constant-time password check in login (dummy bcrypt) | ✅ |
| Registration IP tracking in Redis (3+/24h = flagged) | ✅ |
| Admin JWT tokens (`POST /admin/login`, 60min expiry) | ✅ |
| Admin audit log model + service (`admin_logs` table) | ✅ |
| Admin dependency supports both JWT and legacy `X-Admin-Key` | ✅ |
| `ADMIN_USERNAME` + `ADMIN_PASSWORD_HASH` config | ✅ |
| Migration: `alembic revision --autogenerate -m 'add admin_logs'` | ⏳ |
### ✅ Session 43 Complete — Face Verification (Selfie Video Liveness + Face Match)

| Feature | Status |
|---------|--------|
| InsightFace + OpenCV + ONNX Runtime face detection | ✅ |
| Singleton model loading (lazy, thread-safe) | ✅ |
| Challenge-response liveness (blink, turn_left, turn_right, smile, nod) | ✅ |
| Eye Aspect Ratio (EAR) blink detection | ✅ |
| Head pose estimation (yaw/pitch/roll via solvePnP) | ✅ |
| Smile detection via mouth ratio | ✅ |
| 512-d face embedding extraction + averaging | ✅ |
| Cosine similarity comparison against profile photos | ✅ |
| Redis-based challenge state (10-min TTL) | ✅ |
| Redis-based daily attempt limiting (3/day) | ✅ |
| Redis-based cooldown (24h after success) | ✅ |
| `UserProfile.verified_at` column + migration | ✅ |
| `PhotoService.download_photo_bytes()` for MinIO download | ✅ |
| Admin test endpoint for pipeline debugging | ✅ |
| Auto-face-verify bypass disabled | ✅ |

#### New Files
| File | Purpose |
|------|---------|
| `app/services/face_verification_service.py` | Core engine: model, liveness, embeddings, comparison |
| `app/api/v1/endpoints/verify.py` | User-facing verification endpoints |
| `app/api/v1/endpoints/test_face_verification.py` | Admin test/debug endpoint |
| `app/schemas/verify.py` | Pydantic schemas for verification |
| `alembic/versions/b3c4d5e6f7a8_add_verified_at.py` | Migration for verified_at |

#### Modified Files
| File | Change |
|------|--------|
| `requirements.txt` | Added insightface, opencv-python-headless, onnxruntime |
| `app/core/config.py` | Added 16 FACE_VERIFICATION_* settings |
| `app/models/user_profile.py` | Added `verified_at` column |
| `app/services/photo_service.py` | Added `download_photo_bytes()` |
| `app/main.py` | Registered verify + test_face_verification routers |
| `app/api/v1/endpoints/admin_photos.py` | `_AUTO_FACE_VERIFY = False` |

#### New Endpoints
| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/users/me/verify` | POST | User | Submit selfie image for verification |
| `/users/me/verify/status` | GET | User | Check verification status + cooldown |
| `/admin/face-verification/test` | POST | Admin | Test full pipeline with debug output |

#### Configuration (in .env)
| Setting | Default | Description |
|---------|---------|-------------|
| `FACE_MATCH_THRESHOLD` | `0.5` | Cosine similarity threshold |
| `FACE_DET_SCORE_THRESHOLD` | `0.5` | YuNet detection score threshold |
| `FACE_MODEL_THREADS` | `2` | onnxruntime intra-op threads |
| `FACE_VERIFICATION_MAX_SIZE_MB` | `10` | Max selfie image size |
| `FACE_VERIFICATION_COOLDOWN_TTL` | `86400` | Cooldown between attempts (24h) |
| `FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY` | `10` | Daily attempt limit |
| `FACE_VERIFICATION_MIN_PHOTOS` | `1` | Min photos before selfie verify |
| `FACE_REFERENCE_CACHE_TTL` | `604800` | Cached face embedding TTL (7 days) |

Face verification is image-based: YuNet detects a single face, the face is aligned to a
canonical 112x112 crop, and the InsightFace `w600k_mbf` ONNX model (MobileFaceNet trained
on WebFace600K) extracts a 512-d embedding. The selfie embedding is compared by cosine
similarity against every profile photo; all photos must match. An already-verified account
is still re-checked against its CURRENT photos — a mismatch revokes the badge.

#### Tests (28 total)
| Test Class | Count | Coverage |
|------------|-------|----------|
| TestChallengeGeneration | 6 | Challenge gen, Redis storage, already verified, no auth, cooldown, daily limit |
| TestVerificationStatus | 4 | Not verified, already verified, cooldown active, no auth |
| TestVideoSubmission | 12 | No challenge_id, expired, mismatch, too short, too large, already verified, no photos, liveness fail, low similarity, success, cooldown set, no auth |
| TestFaceVerificationService | 6 | Cosine similarity (3 cases), EAR calculation, challenge types, config settings |

### ✅ Session 44 Complete — DailyLimit Race Condition Fix + SQL Logging Cleanup

| Feature | Status |
|---------|--------|
| `reward_service.get_or_create_daily_limit` — race condition fix using `INSERT ... ON CONFLICT DO UPDATE` | ✅ |
| `chat_service.get_or_create_daily_limit` — same race condition fix | ✅ |
| `session.py` — `echo=False` to suppress duplicate SQL query logs | ✅ |
| Added `sqlalchemy.dialects.postgresql.insert` import to both services | ✅ |
| Constraint name `uq_daily_limits_user_date` used in `on_conflict_do_update` | ✅ |

**Root cause:** Two concurrent requests both SELECT (find nothing), both INSERT, second fails with `UniqueViolationError`. Fixed with PostgreSQL atomic upsert.

**Files Modified:**
| File | Change |
|------|--------|
| `app/services/reward_service.py` | Replaced SELECT-then-INSERT with upsert |
| `app/services/chat_service.py` | Replaced SELECT-then-INSERT with upsert |
| `app/db/session.py` | `echo=settings.DEBUG` → `echo=False` |

### ✅ Session 45 Complete — DailyLimit Upsert Fix

| Feature | Status |
|---------|--------|
| Fixed `get_or_create_daily_limit` — `on_conflict_do_update(set_={})` → `on_conflict_do_nothing()` | ✅ |
| SQLAlchemy raises `ValueError: set parameter dictionary must not be empty` on empty `set_={}` | ✅ |
| `/rewards/my-limits` endpoint now returns correct limits (20 likes / 10 chats) | ✅ |
| `consume_like()` and `consume_chat()` no longer crash on upsert | ✅ |
| Verified via live API call: `daily_likes_limit: 20`, `daily_chats_limit: 10` | ✅ |

**Root cause:** Session 44 introduced `on_conflict_do_update(set_={})` to fix a race condition, but SQLAlchemy requires a non-empty `set_` dictionary. This caused a `ValueError` on every call to `get_or_create_daily_limit()`, crashing all daily limit operations.

**Fix:** Single-line change in `app/services/reward_service.py:55-57` — replaced `on_conflict_do_update(constraint=..., set_={})` with `on_conflict_do_nothing(constraint=...)`.

**Files Modified:**
| File | Change |
|------|--------|
| `app/services/reward_service.py` | `on_conflict_do_update(set_={})` → `on_conflict_do_nothing()` |
| `dev.md` | Session 45 documentation |

### ✅ Session 47 Complete — Race Condition Audit & Fixes

Full codebase audit for race conditions. Found and fixed remaining medium+low severity issues (critical and high were already fixed in sessions 44-45).

**Already Fixed (confirmed):**
| Item | Status |
|------|--------|
| `chat_service.get_or_create_daily_limit` — `on_conflict_do_nothing` | ✅ Session 45 |
| `reward_service.consume_like` — atomic UPDATE with WHERE | ✅ Session 44 |
| `reward_service.consume_chat` — atomic UPDATE with WHERE | ✅ Session 44 |
| `reward_service.claim_ad_reward` — atomic UPDATE with WHERE | ✅ Session 44 |
| `ReferralReward` — `UniqueConstraint("invited_id")` | ✅ Already present |

**Fixes Applied (Session 47):**

| Severity | File | Issue | Fix |
|----------|------|-------|-----|
| Medium | `photos.py:90-99` | Photo upload TOCTOU — SELECT count then INSERT | Added `SELECT ... FOR UPDATE` to lock rows during count check |
| Medium | `photos.py:193-209` | Photo delete double commit — separate commit for delete + set-main | Merged into single transaction (one `session.commit()`) |
| Low | `reports.py:62-64` | Redis incr+expire TOCTOU — key may persist without TTL | Replaced with `pipeline()` for atomic incr+expire |
| Low | `messages.py:258-260` | Redis incr+expire TOCTOU | Same pipeline fix |
| Low | `auth.py:196-198` | Redis incr+expire TOCTOU | Same pipeline fix |
| Low | `verify.py:176,237,277` | Redis incr+expire TOCTOU (3 locations) | Same pipeline fix |

**Files Modified:**
| File | Change |
|------|--------|
| `app/api/v1/endpoints/photos.py` | `with_for_update()` on photo count check; single transaction for delete+set-main |
| `app/api/v1/endpoints/reports.py` | `incr`+`expire` → `pipeline()` |
| `app/api/v1/endpoints/messages.py` | `incr`+`expire` → `pipeline()` |
| `app/api/v1/endpoints/auth.py` | `incr`+`expire` → `pipeline()` |
| `app/api/v1/endpoints/verify.py` | `incr`+`expire` → `pipeline()` (3 locations) |

**Verification:**
- All imports pass (no syntax errors)
- Tests require Docker containers (PostgreSQL on port 5433, Redis on port 6380)

### ✅ Session 48 Complete — Search API Parity with Discover

Search endpoint now returns the same response shape as discover.

**Fields Added to SearchProfileResponse:**
| Field | Type | Description |
|-------|------|-------------|
| `sexual_orientation` | string? | User's sexual orientation |
| `photos` | List<string>? | All approved photo URLs (sorted by order) |
| `interests` | List<string>? | Interest names |
| `prompts` | List<dict>? | Prompt answers with question text |
| `is_online` | bool? | True if last_seen within 5 minutes |

**Fields Removed from SearchProfileResponse:**
| Field | Reason |
|-------|--------|
| `hide_last_seen` | Replaced by privacy-aware `last_seen_at` (null when hidden) |
| `hide_online_status` | Replaced by privacy-aware `is_online` (null when hidden) |

**Files Modified:**
| File | Change |
|------|--------|
| `app/schemas/search.py` | Updated `SearchProfileResponse` to match discover's `ProfileResponse` |
| `app/api/v1/endpoints/search.py` | Added `UserPrompt` eager load, populate sexual_orientation/photos/interests/prompts/is_online, privacy-aware last_seen_at/is_online |
| `dev.md` | Session 48 notes |

### ✅ Session 50 Complete — Demo Bug Fixes (Error Logging, DailyLimit Crash, Flaky Test)

**Fixes from demo server error logs:**

| # | Issue | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | `database_session_rollback` logged at ERROR for 401/403/429 | `session.py` caught all exceptions including expected HTTP client errors | Added `except StarletteHTTPException: raise` before general `except Exception` — also catches `RateLimitExceeded` (429) from slowapi |
| 2 | `NoResultFound` in `get_or_create_daily_limit` | `on_conflict_do_nothing()` + `.returning()` returns no rows on conflict, but `.scalar_one()` requires exactly one | Changed to `.scalar_one_or_none()` with fallback SELECT to fetch existing row |
| 3 | `test_verify_challenge_type_mismatch` test hangs/flakes | Hardcoded `"turn_left"` matches random challenge 20% chance → code proceeds to OpenCV video processing and hangs | Dynamic wrong type: picks a type guaranteed to differ from the generated challenge |

**Files Modified:**
| File | Change |
|------|--------|
| `app/db/session.py` | `except HTTPException: raise` → `except StarletteHTTPException: raise` (catches both fastapi.HTTPException + slowapi RateLimitExceeded) |
| `app/services/reward_service.py` | `.scalar_one()` → `.scalar_one_or_none()` + fallback SELECT when row exists |
| `tests/test_face_verification.py` | Hardcoded `"turn_left"` → dynamic `wrong_type` based on generated challenge |

### ✅ Session 49 Complete — Live Likes/Chats Remaining in API Responses

Swipe and message endpoints now return updated daily limit counts so the client stays in sync without extra API calls.

**Changes:**

| Endpoint | Response Field | Behavior |
|----------|---------------|----------|
| `POST /swipes` | `likes_remaining_today` | Always returned (both like and pass), null if premium |
| `POST /swipes` | `chats_remaining_today` | **NEW** — always returned, null if premium |
| `POST /messages/{id}/text` | `chats_remaining_today` | **NEW** — returned after new chat consumption, null if premium or matched chat |
| `POST /messages/{id}/photo` | `chats_remaining_today` | null (photo msgs don't consume daily limit) |
| `POST /messages/{id}/voice` | `chats_remaining_today` | null (voice msgs don't consume daily limit) |

**Files Modified:**
| File | Change |
|------|--------|
| `app/schemas/discover.py` | Added `chats_remaining_today` to `SwipeResponse` |
| `app/schemas/message.py` | Added `chats_remaining_today` to `SendMessageResponse` |
| `app/api/v1/endpoints/swipes.py` | Always return both likes + chats remaining after every swipe |
| `app/api/v1/endpoints/messages.py` | Return `chats_remaining_today` after text message send, added `RewardService` import |

---

### ✅ Session 50 Complete — WebSocket Typing Indicators + Presence + Redis Pub/Sub

Full WebSocket architecture upgrade: replaced local dict storage with Redis Pub/Sub for multi-worker support, added typing indicators with 5s TTL, added presence tracking with 60s TTL heartbeat, and wired up typing/typing_stopped events in the chat WebSocket handler.

**Changes:**

| File | Change |
|------|--------|
| `app/core/redis.py` | Added `get_redis` async FastAPI dependency (yields `redis_client`) |
| `app/core/deps.py` | Added `validate_ws_token(token, redis)` helper — JWT decode for WebSocket connections without DB query |
| `app/services/websocket_manager.py` | Full rewrite — Redis Pub/Sub + presence + typing + heartbeat; all methods now take `redis: Redis` param |
| `app/api/v1/websocket/matches.py` | Updated: `validate_ws_token`, `redis=Depends(get_redis)`, `heartbeat` on ping, 40s receive timeout |
| `app/api/v1/websocket/chat.py` | Updated: `validate_ws_token`, `redis=Depends(get_redis)`, `db=Depends(get_db)`, IDOR protection, `typing`/`typing_stopped` handlers, `user_online`/`user_offline` events, `read` receipt handler, `clear_typing` on disconnect |
| `app/api/v1/endpoints/swipes.py` | `_background_match_notification` passes `redis_client` to `broadcast_match()` |
| `app/api/v1/endpoints/messages.py` | `_background_websocket_send` passes `redis_client` to `send_to_match()`; updated all 3 `background_tasks.add_task` calls |
| `tests/conftest.py` | Updated `mock_websocket_manager` fixture to also mock `send_to_match` |
| `tests/test_websocket.py` | Updated 5 existing unit tests for new signatures; added 10 new unit tests for typing, presence, heartbeat |

**Redis keys introduced:**
| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `ws:user:{user_id}` | Pub/Sub channel | — | Per-user message delivery |
| `ws:chat:{match_id}` | Pub/Sub channel | — | Per-match message delivery |
| `online:{user_id}` | String | 60s | Presence — refreshed by heartbeat |
| `typing:{match_id}:{user_id}` | String | 5s | Typing indicator — auto-expires |

**Messages passed over WebSocket:**
- `typing` / `typing_stopped` — other user started/stopped typing
- `user_online` / `user_offline` — other user opened/closed chat
- `messages_read` — read receipts

**Files Modified:**
| File | Change |
|------|--------|
| `app/core/redis.py` | Added `get_redis` dependency |
| `app/core/deps.py` | Added `validate_ws_token` |
| `app/services/websocket_manager.py` | Full rewrite |
| `app/api/v1/websocket/matches.py` | Updated for Redis Pub/Sub + heartbeat |
| `app/api/v1/websocket/chat.py` | Updated for typing/presence/IDOR |
| `app/api/v1/endpoints/swipes.py` | Added `redis_client` to `broadcast_match` call |
| `app/api/v1/endpoints/messages.py` | Added `redis_client` to `send_to_match` call |
| `tests/conftest.py` | Updated mock fixture |
| `tests/test_websocket.py` | 15 tests passing (5 updated + 10 new) |

---

### ✅ Session 52 Complete — Search/Discover: Last Seen Sort + Redis Presence

Added real-time online presence to search and discover, plus `sort_by=last_seen` for search.

| Feature | Status |
|---------|--------|
| `sort_by=last_seen` on `GET /search` — `ORDER BY users.last_seen_at DESC NULLS LAST` | ✅ |
| `last_seen_at` updated on **login** (`auth.py`) | ✅ |
| `last_seen_at` updated on **WebSocket connect + disconnect** (`matches.py`) using standalone `AsyncSessionLocal` | ✅ |
| Search `is_online` uses Redis `online:{user_id}` (60s TTL, set by WS heartbeat) | ✅ |
| Discover `is_online` uses same Redis presence | ✅ |
| Privacy: `hide_online_status` → `is_online=false` (never reveals real presence) | ✅ |
| Privacy: `hide_last_seen` → `last_seen_at=null` | ✅ |
| Sort ignores privacy — uses actual `last_seen_at` DB value | ✅ |
| Search schema: `sort_by` pattern now includes `last_seen` | ✅ |
| Tests: `test_sort_by_last_seen`, `test_is_online_from_redis`, `test_is_offline_when_no_redis_key` | ✅ (3 new) |
| All 68 search + discover tests passing | ✅ |

**Files Modified:**
| File | Change |
|------|--------|
| `app/api/v1/endpoints/auth.py` | `user.last_seen_at = datetime.now(timezone.utc)` on login |
| `app/api/v1/websocket/matches.py` | Connect/disconnect update `last_seen_at` via `AsyncSessionLocal` |
| `app/api/v1/endpoints/search.py` | `last_seen` sort branch; Redis pipeline `EXISTS` for `online:{uid}`; privacy-aware response builder; module-level `import app.core.redis as redis_module` for test fixture swap |
| `app/api/v1/endpoints/discover.py` | Same Redis presence + privacy logic; module-level redis import |
| `app/schemas/search.py` | `sort_by` regex: `^(recent\|distance\|age\|name\|last_seen)$` |
| `tests/test_search.py` | Added `TestSearchOnlineSort` class (3 tests) |

**Files NOT Modified:**
- `app/schemas/discover.py` — already had `last_seen_at` + `is_online` in `ProfileResponse`

### ✅ Session 51 Complete — Docker Health Checks + Multi-Worker

Production-ready Docker configuration: added health check endpoint, dynamic worker count, and Docker health check configuration.

**Changes:**

| File | Change |
|------|--------|
| `app/main.py` | Added `/health/ready` endpoint — checks DB + Redis connectivity (readiness probe) |
| `entrypoint.sh` | Dynamic worker count from `nproc` — formula `(cores × 2) + 1`, capped at 8 |
| `docker-compose.yml` | Added `healthcheck` to `app` service (Python urllib, 30s interval, `start_period: 40s`) |
| `scripts/deploy.sh` | Health URL changed to `/health/ready`, retries increased to 8 |

**Key design decisions:**

| Decision | Rationale |
|----------|-----------|
| Dynamic workers via `nproc` | No hardcoded worker count — adapts to whatever CPU the container has |
| Worker formula `(cores × 2) + 1` | Standard uvicorn recommendation |
| Worker cap at 8 | Prevents excessive memory usage on large machines |
| Health check via `urllib` | No extra packages (curl) needed in Docker image |
| `/health/ready` checks DB + Redis | Docker container marked unhealthy if dependencies are down |
| `start_period: 40s` | Gives entrypoint.sh time to run migrations + seed before first check |
| No resource limits | Docker Compose `deploy.resources` ignored in non-Swarm mode; OS manages memory/CPU |

**Health check flow:**

```
Container starts → entrypoint.sh runs migrations + seeds → uvicorn starts with N workers
→ Docker waits 40s (start_period) → GET /health/ready → DB ✓ + Redis ✓ → healthy
→ Subsequent checks every 30s, 3 retries before unhealthy → Docker restarts
```

**Files Modified:**
| File | Change |
|------|--------|
| `app/main.py` | Added `/health/ready` endpoint + imports (`Depends`, `AsyncSession`, `select`, `redis_client`) |
| `entrypoint.sh` | Dynamic workers from `nproc`, formula `(cores × 2) + 1`, cap at 8 |
| `docker-compose.yml` | Added `healthcheck` to `app` service |
| `scripts/deploy.sh` | Health URL → `/health/ready`, retries → 8 |

---

## Server Setup TODO

- [ ] Purchase VPS (Hetzner CX22 or DigitalOcean droplet — 2 vCPU, 4GB RAM minimum)
- [ ] Install Docker + Docker Compose on VPS
- [ ] Clone repo: `git clone https://github.com/EhsanRezaie/project_d.git /opt/dating-app`
- [ ] Configure `.env` on server (copy `.env.example`, set production secrets)
- [ ] Run initial deploy: `bash scripts/deploy.sh`
- [ ] Set up firewall: `sudo bash scripts/firewall.sh`
- [ ] Add GitHub Secrets for auto-deploy:
  - `VPS_HOST` — server IP
  - `VPS_USERNAME` — SSH user (e.g. `root`)
  - `VPS_SSH_KEY` — private SSH key
