# Performance Improvement Plan
## Iranian Dating App — Backend + Flutter

> **Single source of truth for all performance work.**
> Based on actual OpenAPI spec (`openapi.json`) — every endpoint referenced here is real.
> Work through phases top to bottom. Check off each item as it's done.
> Pass this file to Claude at the start of each session.

---

## Table of Contents

1. [Database Indexes](#1-database-indexes)
2. [Redis Caching Layer](#2-redis-caching-layer)
3. [Backend Query Optimization](#3-backend-query-optimization)
4. [API Response Optimization](#4-api-response-optimization)
5. [Flutter App Performance](#5-flutter-app-performance)
6. [Implementation Order](#6-implementation-order)

---

## 1. Database Indexes

All indexes go into a **single new Alembic migration**:

```bash
alembic revision -m "performance_indexes"
```

Write raw SQL inside the migration's `upgrade()` with `op.execute()`. Do not use
`--autogenerate` — these are manual performance indexes, not schema changes.

### 1.1 — `users` Table

```sql
-- Email lookup on every login + register/init check
CREATE INDEX idx_users_email ON users(email);

-- Token version check inside get_current_user (every authenticated request)
-- Already covered by PK, but useful for partial queries
CREATE INDEX idx_users_is_active ON users(is_active) WHERE is_active = true;

-- registration_status — exclude incomplete profiles from discover/search
CREATE INDEX idx_users_registration_status ON users(registration_status);

-- Referral system — POST /api/v1/referrals/claim looks up referral_code
CREATE INDEX idx_users_referral_code ON users(referral_code);

-- last_seen_at — used in SearchProfileResponse, sorting by recent activity
CREATE INDEX idx_users_last_seen ON users(last_seen_at DESC NULLS LAST);
```

### 1.2 — `user_profiles` Table

```sql
-- GET /api/v1/discover — primary compound filter (gender optional + lat/lng for distance)
CREATE INDEX idx_profiles_gender ON user_profiles(gender);
CREATE INDEX idx_profiles_lat_lng ON user_profiles(lat, lng);

-- birth_date — discover and search both filter by age_min/age_max
-- Query: birth_date BETWEEN (today - age_max years) AND (today - age_min years)
CREATE INDEX idx_profiles_birth_date ON user_profiles(birth_date);

-- GET /api/v1/search — each of these is an independent filter param
CREATE INDEX idx_profiles_country ON user_profiles(country);
CREATE INDEX idx_profiles_province ON user_profiles(province);
CREATE INDEX idx_profiles_city ON user_profiles(city);
CREATE INDEX idx_profiles_religion ON user_profiles(religion);
CREATE INDEX idx_profiles_ethnicity ON user_profiles(ethnicity);
CREATE INDEX idx_profiles_education ON user_profiles(education);
CREATE INDEX idx_profiles_body_type ON user_profiles(body_type);
CREATE INDEX idx_profiles_smoking ON user_profiles(smoking);
CREATE INDEX idx_profiles_drinking ON user_profiles(drinking);
CREATE INDEX idx_profiles_relationship_status ON user_profiles(relationship_status);

-- height_min/height_max filter in GET /api/v1/search
CREATE INDEX idx_profiles_height ON user_profiles(height);

-- is_verified filter in GET /api/v1/search ?is_verified=true
CREATE INDEX idx_profiles_is_verified ON user_profiles(is_verified) WHERE is_verified = true;

-- premium_until — is_premium check in ProfileResponse and SearchProfileResponse
CREATE INDEX idx_profiles_premium_until ON user_profiles(premium_until);
```

### 1.3 — `swipes` Table

```sql
-- GET /api/v1/discover — excludes already-swiped users on every discover load
-- This is the single most-hit exclusion query in the whole app
CREATE INDEX idx_swipes_swiper_swipee ON swipes(swiper_id, swipee_id);

-- POST /api/v1/swipes — mutual like detection (reverse swipe lookup)
CREATE INDEX idx_swipes_swipee_swiper_type ON swipes(swipee_id, swiper_id, swipe_type);

-- GET /api/v1/swipes/stats — aggregate by swiper + type
CREATE INDEX idx_swipes_swiper_type ON swipes(swiper_id, swipe_type);
```

### 1.4 — `matches` Table

```sql
-- GET /api/v1/matches — both user columns queried, sorted by matched_at DESC
CREATE INDEX idx_matches_user1_time ON matches(user1_id, matched_at DESC);
CREATE INDEX idx_matches_user2_time ON matches(user2_id, matched_at DESC);

-- GET /api/v1/messages/{identifier} — resolve match from two user IDs
-- GET /api/v1/discover — excludes already-matched users
CREATE INDEX idx_matches_users_pair ON matches(user1_id, user2_id);

-- GET /api/v1/matches/{match_id} — direct match lookup
-- Covered by PK, no extra index needed
```

### 1.5 — `messages` Table

```sql
-- GET /api/v1/messages/{identifier} — paginated chat history, most frequent query
-- Uses OFFSET pagination currently → sorted by sent_at DESC
CREATE INDEX idx_messages_match_sent ON messages(match_id, sent_at DESC);

-- POST /api/v1/messages/delivered — mark as delivered per receiver
CREATE INDEX idx_messages_receiver_delivered ON messages(receiver_id, is_delivered)
  WHERE is_delivered = false;

-- POST /api/v1/messages/read — mark as read per receiver
CREATE INDEX idx_messages_receiver_read ON messages(receiver_id, is_read)
  WHERE is_read = false;

-- GET /api/v1/matches — last_message field: most recent message per match
CREATE INDEX idx_messages_match_recent ON messages(match_id, sent_at DESC)
  WHERE is_deleted_for_all = false;

-- GET /api/v1/messages/{message_id}/status
-- Covered by PK
```

### 1.6 — `blocks` Table

```sql
-- GET /api/v1/discover + GET /api/v1/search — both exclude blocked users in both directions
-- These subqueries run on every discover/search request
CREATE INDEX idx_blocks_blocker ON blocks(blocker_id);
CREATE INDEX idx_blocks_blocked ON blocks(blocked_id);

-- GET /api/v1/blocks — list blocks for current user
-- POST /api/v1/blocks/{user_id}/unblock — check existence before delete
CREATE INDEX idx_blocks_pair ON blocks(blocker_id, blocked_id);
```

### 1.7 — `notifications` Table

```sql
-- GET /api/v1/notifications — list per user, unread first, paginated
CREATE INDEX idx_notifications_user_unread ON notifications(user_id, is_read, created_at DESC);
```

### 1.8 — `daily_limits` Table

```sql
-- POST /api/v1/swipes — check likes_used before allowing swipe (every swipe)
-- POST /api/v1/messages/{identifier}/text — check chats_used before allowing message
-- GET /api/v1/rewards/my-limits — read current usage
CREATE INDEX idx_daily_limits_user_date ON daily_limits(user_id, date);
```

### 1.9 — `photos` Table

```sql
-- GET /api/v1/users/me + ProfileResponse + DiscoverResponse — main_photo_url lookup
CREATE INDEX idx_photos_user_main ON photos(user_id, is_main) WHERE is_main = true;

-- GET /api/v1/users/me/photos — list all photos for current user
CREATE INDEX idx_photos_user_id ON photos(user_id, created_at DESC);

-- GET /api/v1/admin/photos/pending — moderation queue
CREATE INDEX idx_photos_status ON photos(status, created_at DESC);
```

### 1.10 — `user_interests` + `user_prompts` Tables

```sql
-- GET /api/v1/search ?interests=... — AND condition across multiple interests
CREATE INDEX idx_user_interests_user ON user_interests(user_id);
CREATE INDEX idx_user_interests_interest ON user_interests(interest_id);

-- Profile loads — interests and prompts loaded per user
CREATE INDEX idx_user_prompts_user ON user_prompts(user_id);
```

### 1.11 — Verify Indexes Are Working

After migration, run `EXPLAIN ANALYZE` on the two heaviest queries:

```sql
-- Discover query verification
EXPLAIN ANALYZE
SELECT u.id FROM users u
JOIN user_profiles p ON p.user_id = u.id
WHERE u.registration_status = 'onboarding_complete'
  AND u.is_active = true
  AND p.gender = 'female'
  AND p.birth_date BETWEEN '1994-01-01' AND '2004-01-01'
  AND u.id NOT IN (SELECT swipee_id FROM swipes WHERE swiper_id = '<your_id>')
  AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = '<your_id>')
LIMIT 20;
```

Look for `Index Scan` instead of `Seq Scan`. If `Seq Scan` still appears on any table
with 1000+ rows, add a targeted index for that column.

---

## 2. Redis Caching Layer

### 2.1 — New File: `app/core/cache.py`

Create this file. All caching helpers live here.

```python
import json
from uuid import UUID
from redis.asyncio import Redis

# ── TTLs ──────────────────────────────────────────────────────────────────────
TTL_INTERESTS       = 86400      # 24h  — seed data, never changes at runtime
TTL_PROMPTS         = 86400      # 24h  — seed data, never changes at runtime
TTL_LOCATIONS       = 604800     # 7d   — countries/provinces/cities
TTL_SYSTEM_STATUS   = 60         # 60s  — /system/status
TTL_SUB_PLANS       = 3600       # 1h   — /subscriptions/plans
TTL_USER_PROFILE    = 600        # 10m  — /users/me
TTL_USER_PHOTOS     = 600        # 10m  — /users/me/photos
TTL_DAILY_LIMITS    = None       # dynamic — until midnight


# ── Cache Keys ────────────────────────────────────────────────────────────────
def key_interests() -> str:
    return "cache:interests:all"

def key_prompts(language: str) -> str:
    return f"cache:prompts:{language}"

def key_countries() -> str:
    return "cache:locations:countries"

def key_provinces(country: str) -> str:
    return f"cache:locations:provinces:{country}"

def key_cities(country: str, province: str) -> str:
    return f"cache:locations:cities:{country}:{province}"

def key_system_status() -> str:
    return "cache:system:status"

def key_sub_plans() -> str:
    return "cache:subscriptions:plans"

def key_user_profile(user_id: UUID) -> str:
    return f"cache:user:{user_id}:profile"

def key_user_photos(user_id: UUID) -> str:
    return f"cache:user:{user_id}:photos"

def key_daily_limits(user_id: UUID, date: str) -> str:
    return f"cache:limits:{user_id}:{date}"


# ── Helpers ───────────────────────────────────────────────────────────────────
async def cache_get(redis: Redis, key: str):
    """Get and deserialize a cached value. Returns None on miss."""
    raw = await redis.get(key)
    return json.loads(raw) if raw else None

async def cache_set(redis: Redis, key: str, value, ttl: int):
    """Serialize and store a value with TTL."""
    await redis.setex(key, ttl, json.dumps(value, default=str))

async def invalidate_user_cache(redis: Redis, user_id: UUID):
    """
    Call this at the end of every endpoint that mutates user data:
      PUT  /api/v1/users/me
      PUT  /api/v1/users/me/photos/{photo_id}/main
      POST /api/v1/users/me/photos
      DELETE /api/v1/users/me/photos/{photo_id}
      POST /api/v1/users/me/location
      PATCH /api/v1/users/me/location-text
      PATCH /api/v1/locations/me/location-gps
      PATCH /api/v1/locations/me/location-manual
      PUT  /api/v1/users/me/interests    (if exists)
      PUT  /api/v1/users/me/prompts      (if exists)
    """
    keys = [
        key_user_profile(user_id),
        key_user_photos(user_id),
    ]
    await redis.delete(*keys)
```

### 2.2 — Static / Seed Data (Highest ROI)

These endpoints serve data that **never changes at runtime**. Every app launch hits them.

| Endpoint | Cache Key | TTL | Invalidate On |
|----------|-----------|-----|---------------|
| `GET /api/v1/interests` | `cache:interests:all` | 24h | Never (manual only) |
| `GET /api/v1/prompts?language=fa` | `cache:prompts:fa` | 24h | Never |
| `GET /api/v1/prompts?language=en` | `cache:prompts:en` | 24h | Never |
| `GET /api/v1/locations/countries` | `cache:locations:countries` | 7d | Never |
| `GET /api/v1/locations/provinces?country=X` | `cache:locations:provinces:{country}` | 7d | Never |
| `GET /api/v1/locations/cities?country=X` | `cache:locations:cities:{country}:{province}` | 7d | Never |
| `GET /api/v1/subscriptions/plans` | `cache:subscriptions:plans` | 1h | Plan change only |

**Implementation pattern (same for all of them):**

```python
# Example: endpoints/interests.py
from app.core.cache import cache_get, cache_set, key_interests, TTL_INTERESTS

@router.get("/interests")
async def get_interests(redis: Redis = Depends(get_redis), db: AsyncSession = Depends(get_db)):
    cached = await cache_get(redis, key_interests())
    if cached:
        return cached

    result = await db.execute(
        select(Interest).order_by(Interest.category, Interest.name)
    )
    data = [i.to_dict() for i in result.scalars()]
    await cache_set(redis, key_interests(), data, TTL_INTERESTS)
    return data
```

### 2.3 — System Status Cache

`GET /api/v1/system/status` checks DB + Redis + MinIO on every app launch (splash screen).
Cache the result for 60 seconds.

```python
# endpoints/system.py — inside get_system_status()
cached = await cache_get(redis, key_system_status())
if cached:
    return cached

status = await _build_status(db, redis)   # existing logic
await cache_set(redis, key_system_status(), status, TTL_SYSTEM_STATUS)
return status
```

### 2.4 — `GET /api/v1/users/me` Cache

Every screen that needs the current user hits this endpoint. Cache per user, invalidate
on any mutation.

```python
# endpoints/users.py — inside get_me()
cache_key = key_user_profile(current_user.id)
cached = await cache_get(redis, cache_key)
if cached:
    return UserProfileResponse(**cached)

response = build_user_profile_response(current_user)
await cache_set(redis, cache_key, response.dict(), TTL_USER_PROFILE)
return response
```

Invalidate in: `PUT /users/me`, `POST /users/me/location`, `PATCH /users/me/location-text`,
`PATCH /locations/me/location-gps`, `PATCH /locations/me/location-manual`.

### 2.5 — Daily Limits Cache

`POST /api/v1/swipes` and `POST /api/v1/messages/{identifier}/text` both check
`daily_limits` before allowing the action. This is 2 DB reads per swipe and per message send.

Cache the current day's limit record in Redis. TTL = seconds until midnight.

```python
# In reward_service.py or wherever limits are checked
import datetime

def _seconds_until_midnight() -> int:
    now = datetime.datetime.now()
    midnight = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())

async def get_daily_limits(user_id: UUID, redis: Redis, db: AsyncSession):
    today = datetime.date.today().isoformat()
    cache_key = key_daily_limits(user_id, today)

    cached = await cache_get(redis, cache_key)
    if cached:
        return cached

    limits = await db.execute(
        select(DailyLimit).where(
            DailyLimit.user_id == user_id,
            DailyLimit.date == datetime.date.today()
        )
    )
    data = limits.scalar_one_or_none()
    result = data.to_dict() if data else {"likes_used": 0, "chats_used": 0, "ad_rewards_used": 0}
    await cache_set(redis, cache_key, result, _seconds_until_midnight())
    return result
```

After each swipe or message, update both Redis and DB:

```python
await redis.setex(cache_key, _seconds_until_midnight(), json.dumps(updated_limits))
# then write to DB async (background task)
```

---

## 3. Backend Query Optimization

### 3.1 — Two `get_current_user` Dependency Variants

**Problem:** `deps.py` currently runs a full DB query with `selectinload` of profile +
settings on **every single authenticated request**, including endpoints that only need
`user.id` (e.g. `POST /swipes`, `POST /messages/delivered`, `POST /messages/read`,
`POST /notifications/read`, `POST /blocks/{user_id}/block`).

**Fix:** Add a lightweight variant:

```python
# app/core/deps.py

async def get_current_user_id(
    token: str = Depends(oauth2_scheme),
) -> UUID:
    """
    Validates token only. Returns user_id. Zero DB queries.
    Use for: POST /swipes, POST /messages/delivered, POST /messages/read,
             POST /notifications/read, POST /blocks/{user_id}/block,
             POST /blocks/{user_id}/unblock, DELETE /notifications/{id},
             DELETE /messages/{message_id}
    """
    payload = decode_access_token(token)   # existing JWT decode
    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exception
    return UUID(user_id)
```

Switch these endpoints from `get_current_user` to `get_current_user_id`:

| Endpoint | Reason |
|----------|--------|
| `POST /api/v1/swipes` | Only needs user_id to record the swipe |
| `POST /api/v1/messages/delivered` | Only needs user_id to mark delivery |
| `POST /api/v1/messages/read` | Only needs user_id to mark read |
| `POST /api/v1/notifications/read` | Only needs user_id |
| `DELETE /api/v1/notifications/{notification_id}` | Only needs user_id |
| `DELETE /api/v1/messages/{message_id}` | Only needs user_id |
| `POST /api/v1/blocks/{user_id}/block` | Only needs user_id |
| `POST /api/v1/blocks/{user_id}/unblock` | Only needs user_id |
| `POST /api/v1/reports/{user_id}` | Only needs user_id |

### 3.2 — Eager Loading on List Endpoints

**Problem:** `GET /api/v1/discover`, `GET /api/v1/search`, `GET /api/v1/matches`
return lists of users. Without explicit `selectinload`, SQLAlchemy fires a separate
query for each user's profile, photo, and settings — classic N+1.

`DiscoverResponse` uses `ProfileResponse` (fields: id, name, age, gender, bio, height,
weight, distance_km, main_photo_url, is_premium, is_verified).

`SearchProfileResponse` uses more fields including last_seen_at from settings.

`MatchResponse` uses `MatchUserResponse` (id, name, age, main_photo_url) + `LastMessageResponse`.

**Fix:** Apply `selectinload` chains to all list queries:

```python
# For discover and search — load only what ProfileResponse/SearchProfileResponse needs
from sqlalchemy.orm import selectinload

stmt = (
    select(User)
    .join(User.profile)
    .options(
        selectinload(User.profile),
        selectinload(User.photos.and_(Photo.is_main == True)),  # main photo only
        selectinload(User.settings),   # needed for hide_last_seen, hide_online_status
    )
    .where(...)
)

# For matches — MatchUserResponse only needs name, age, main_photo_url
stmt = (
    select(Match)
    .options(
        selectinload(Match.user1).options(
            selectinload(User.profile),
            selectinload(User.photos.and_(Photo.is_main == True)),
        ),
        selectinload(Match.user2).options(
            selectinload(User.profile),
            selectinload(User.photos.and_(Photo.is_main == True)),
        ),
        selectinload(Match.messages.and_(Message.sent_at == last_msg_subq)),  # last message
    )
    .where(...)
)
```

### 3.3 — Haversine Distance: Move Computation Into PostgreSQL

**Problem:** `GET /api/v1/discover` and `GET /api/v1/search` both accept `distance_km`.
If distance filtering happens in Python, all users in the DB are loaded first, then filtered
— rendering the `LIMIT` useless.

**Fix:** Push Haversine into the WHERE clause so the DB does the filtering before LIMIT:

```python
# app/api/v1/endpoints/discover.py
from sqlalchemy import func, and_

def haversine_distance(lat1, lng1, lat2_col, lng2_col):
    """Returns distance in km as a SQLAlchemy expression."""
    return (
        6371 * func.acos(
            func.cos(func.radians(lat1))
            * func.cos(func.radians(lat2_col))
            * func.cos(func.radians(lng2_col) - func.radians(lng1))
            + func.sin(func.radians(lat1))
            * func.sin(func.radians(lat2_col))
        )
    )

# In the query:
if distance_km and current_user.profile.lat:
    distance_expr = haversine_distance(
        current_user.profile.lat,
        current_user.profile.lng,
        UserProfile.lat,
        UserProfile.lng
    )
    stmt = stmt.where(distance_expr <= distance_km)
    stmt = stmt.add_columns(distance_expr.label("distance_km"))
```

### 3.4 — Background Tasks for Non-Critical Work

**Problem:** After `POST /api/v1/swipes` creates a match, it synchronously creates a
notification and updates last_seen_at — blocking the swipe response.
Same issue in `POST /api/v1/messages/{identifier}/text`.

**Fix:** Use FastAPI `BackgroundTasks`:

```python
# endpoints/swipes.py
@router.post("/swipes")
async def create_swipe(
    ...,
    background_tasks: BackgroundTasks,
):
    result = await swipe_service.record_swipe(...)
    if result.is_match:
        background_tasks.add_task(
            notification_service.send_match_notification,
            user_id=current_user_id,
            matched_user_id=swipee_id,
            db=db
        )
    background_tasks.add_task(update_last_seen, current_user_id, db)
    return result
```

Apply to these endpoints:

| Endpoint | Background Work to Offload |
|----------|---------------------------|
| `POST /api/v1/swipes` | Match notification, last_seen update |
| `POST /api/v1/messages/{identifier}/text` | Message notification, last_seen update |
| `POST /api/v1/messages/{identifier}/photo` | Message notification, last_seen update |
| `POST /api/v1/messages/{identifier}/voice` | Message notification, last_seen update |
| `POST /api/v1/reports/{user_id}` | Admin notification |

### 3.5 — Cursor-Based Pagination for `GET /messages/{identifier}`

**Problem:** `GET /api/v1/messages/{identifier}` currently uses `limit` + `offset`.
With 500+ messages, `OFFSET 480` forces the DB to scan and discard 480 rows before
returning 20. Chat history grows indefinitely.

**Fix:** Add `before` cursor parameter (ISO datetime). Keep `offset` as fallback for
backwards compatibility.

```python
# endpoints/messages.py
@router.get("/messages/{identifier}")
async def get_messages(
    identifier: str,
    limit: int = Query(default=30, ge=1, le=50),
    before: Optional[datetime] = Query(default=None),  # NEW cursor param
    offset: int = Query(default=0),                     # keep for compatibility
    ...
):
    stmt = (
        select(Message)
        .where(Message.match_id == match_id)
        .order_by(Message.sent_at.desc())
        .limit(limit)
    )
    if before:
        stmt = stmt.where(Message.sent_at < before)   # cursor
    else:
        stmt = stmt.offset(offset)                     # legacy fallback
```

Flutter uses the `sent_at` of the oldest loaded message as the next `before` cursor.

### 3.6 — Enforce `limit` Cap on All List Endpoints

Prevent clients from requesting huge pages accidentally:

| Endpoint | Default | Max |
|----------|---------|-----|
| `GET /api/v1/discover` | 20 | 50 |
| `GET /api/v1/search` | 20 | 50 |
| `GET /api/v1/matches` | 20 | 50 |
| `GET /api/v1/messages/{identifier}` | 30 | 50 |
| `GET /api/v1/notifications` | 20 | 50 |
| `GET /api/v1/blocks` | 20 | 50 |

```python
limit: int = Query(default=20, ge=1, le=50)
```

---

## 4. API Response Optimization

### 4.1 — GZip Middleware

Add to `main.py`. Reduces JSON response size ~60–75% for list endpoints. One line.

```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

### 4.2 — `Cache-Control` Headers on Public Static Endpoints

These endpoints require no auth and return data that doesn't change. Tell the client
and any CDN/proxy to cache them.

```python
from fastapi import Response

# GET /api/v1/interests
@router.get("/interests")
async def get_interests(response: Response, ...):
    response.headers["Cache-Control"] = "public, max-age=86400"
    ...

# GET /api/v1/prompts
@router.get("/prompts")
async def get_prompts(response: Response, ...):
    response.headers["Cache-Control"] = "public, max-age=86400"
    ...

# GET /api/v1/locations/countries
@router.get("/locations/countries")
async def get_countries(response: Response, ...):
    response.headers["Cache-Control"] = "public, max-age=604800"
    ...

# GET /api/v1/subscriptions/plans
@router.get("/subscriptions/plans")
async def get_plans(response: Response, ...):
    response.headers["Cache-Control"] = "public, max-age=3600"
    ...

# GET /api/v1/system/status
@router.get("/system/status")
async def get_status(response: Response, ...):
    response.headers["Cache-Control"] = "public, max-age=60"
    ...
```

**Never add `Cache-Control: public` to any authenticated (🔒) endpoint.**

### 4.3 — `DiscoverResponse` Is Already Slim ✅

`ProfileResponse` (used by `GET /api/v1/discover`) already contains only:
`id, name, age, gender, bio, height, weight, distance_km, main_photo_url, is_premium, is_verified`

No change needed here — the schema is well-designed.

`SearchProfileResponse` (used by `GET /api/v1/search`) is heavier by design (more filters
shown in results). This is correct behaviour — no change needed.

---

## 5. Flutter App Performance

### 5.1 — HTTP Response Caching with `dio_cache_interceptor`

Add to `pubspec.yaml`:

```yaml
dependencies:
  dio_cache_interceptor: ^3.4.4
  dio_cache_interceptor_hive_store: ^3.2.1
  hive: ^2.2.3
```

Configure in `api_service.dart`:

```dart
import 'package:dio_cache_interceptor/dio_cache_interceptor.dart';
import 'package:dio_cache_interceptor_hive_store/dio_cache_interceptor_hive_store.dart';
import 'package:path_provider/path_provider.dart';

// In ApiService.init():
final dir = await getApplicationDocumentsDirectory();
final store = HiveCacheStore('${dir.path}/dio_cache');

final _cacheOptions = CacheOptions(
  store: store,
  policy: CachePolicy.networkFirst,
  hitCacheOnErrorExcept: [401, 403],
  maxStale: const Duration(minutes: 5),
);

dio.interceptors.add(DioCacheInterceptor(options: _cacheOptions));
```

Per-request cache policy overrides:

| Endpoint | Policy | Max Stale |
|----------|--------|-----------|
| `GET /api/v1/interests` | `CacheFirst` | 24 hours |
| `GET /api/v1/prompts` | `CacheFirst` | 24 hours |
| `GET /api/v1/locations/countries` | `CacheFirst` | 7 days |
| `GET /api/v1/locations/provinces` | `CacheFirst` | 7 days |
| `GET /api/v1/locations/cities` | `CacheFirst` | 7 days |
| `GET /api/v1/subscriptions/plans` | `NetworkFirst` | 1 hour |
| `GET /api/v1/system/status` | `NetworkFirst` | 60 seconds |
| `GET /api/v1/users/me` | `NetworkFirst` | 5 minutes |
| `GET /api/v1/discover` | `NoCache` | — |
| `GET /api/v1/search` | `NoCache` | — |
| `GET /api/v1/matches` | `NetworkFirst` | 2 minutes |

```dart
// Usage example for interests:
final response = await dio.get(
  '/interests',
  options: _cacheOptions.copyWith(
    policy: CachePolicy.forceCache,
    maxStale: const Nullable(Duration(hours: 24)),
  ).toOptions(),
);
```

### 5.2 — `CachedNetworkImage` — Proper Size Constraints

Already in the stack. Make sure it's configured everywhere photos appear:

```dart
CachedNetworkImage(
  imageUrl: photoUrl,
  // Limit decoded image size in memory (prevents OOM on list screens)
  memCacheWidth: 400,
  memCacheHeight: 400,
  // Limit disk cache — avoid filling device storage
  maxWidthDiskCache: 800,
  maxHeightDiskCache: 800,
  placeholder: (context, url) => const _ShimmerAvatar(),
  errorWidget: (context, url, _) => const _DefaultAvatar(),
  fadeInDuration: const Duration(milliseconds: 200),
)

// Shimmer placeholder widget:
class _ShimmerAvatar extends StatelessWidget {
  const _ShimmerAvatar();
  @override
  Widget build(BuildContext context) {
    return Shimmer.fromColors(
      baseColor: Colors.grey[300]!,
      highlightColor: Colors.grey[100]!,
      child: Container(color: Colors.white),
    );
  }
}
```

Add `shimmer: ^3.0.0` to `pubspec.yaml`.

### 5.3 — `Selector` Instead of `Consumer` in Hot-Rebuild Paths

**Problem:** `Consumer<AuthProvider>` and `Consumer<ProfileProvider>` rebuild entire
widget subtrees when any property changes, even unrelated ones.

**Fix:** Use `Selector` to rebuild only on the specific field that changed.

```dart
// Before (rebuilds everything when any auth state changes):
Consumer<AuthProvider>(
  builder: (context, auth, _) => Text(auth.user?.name ?? ''),
)

// After (rebuilds only when name changes):
Selector<AuthProvider, String?>(
  selector: (_, auth) => auth.user?.name,
  builder: (context, name, _) => Text(name ?? ''),
)
```

Apply in:
- Profile screen (name, photo, premium badge)
- Main screen bottom nav (notification badge count)
- Chat list (last message, unread count per match)
- Any widget that reads a single field from a large provider

### 5.4 — `ListView.builder` + `RepaintBoundary` on All List Screens

All scrollable lists must use `ListView.builder` (lazy) not `ListView(children: [...])`.
Wrap each list item in `RepaintBoundary` to isolate repaints:

```dart
// Matches list, Search results, Notifications list, Blocks list:
ListView.builder(
  itemCount: items.length,
  itemBuilder: (context, index) {
    return RepaintBoundary(
      key: ValueKey(items[index].id),
      child: MatchCard(match: items[index]),
    );
  },
)
```

For discover swipe cards, wrap each card widget in `RepaintBoundary` too.

### 5.5 — Parallelize Splash Screen Async Calls

`GET /api/v1/system/status` and `POST /api/v1/system/version-check` are called on every
cold start. The token load from `flutter_secure_storage` can happen in parallel.

```dart
// splash_screen.dart — initState or didChangeDependencies
Future<void> _initialize() async {
  // Run all startup tasks in parallel
  final results = await Future.wait([
    systemService.getStatus(),                          // GET /system/status
    systemService.checkVersion(platform, appVersion),  // POST /system/version-check
    storageService.loadTokens(),                        // flutter_secure_storage read
  ]);

  final status = results[0] as SystemStatus;
  final versionCheck = results[1] as VersionCheckResponse;

  if (status.maintenance) { _showMaintenance(); return; }
  if (versionCheck.status == 'update_required') { _showForceUpdate(); return; }

  // Navigate based on token presence
  final hasToken = results[2] as bool;
  Navigator.pushReplacementNamed(context, hasToken ? '/home' : '/login');
}
```

### 5.6 — WebSocket Reconnection with Exponential Backoff

The chat WebSocket (`/api/v1/ws/chat`) has no reconnection strategy currently.
Add exponential backoff in `services/api_service.dart` or a dedicated `websocket_service.dart`:

```dart
int _retryCount = 0;
Timer? _retryTimer;

void _onDisconnected() {
  if (_retryCount >= 6) return;  // give up after ~60s total

  final delay = Duration(seconds: min(30, 1 << _retryCount));  // 1,2,4,8,16,30
  _retryTimer = Timer(delay, () {
    _retryCount++;
    _connect();
  });
}

void _onConnected() {
  _retryCount = 0;  // reset on successful connection
  _retryTimer?.cancel();
}
```

### 5.7 — Paginate `GET /api/v1/notifications`

The notifications screen should load 20 at a time and append more on scroll,
not load all notifications at once.

```dart
// notification_provider.dart
int _offset = 0;
bool _hasMore = true;
List<Notification> _notifications = [];

Future<void> loadMore() async {
  if (!_hasMore) return;
  final result = await notificationService.getNotifications(
    limit: 20,
    offset: _offset,
  );
  _notifications.addAll(result.items);
  _offset += result.items.length;
  _hasMore = result.items.length == 20;
  notifyListeners();
}
```

---

## 6. Implementation Order

Work through phases in order. Each phase is ~1 session of work.

### ✅ Phase 1 — Database Indexes *(do first, zero risk, highest ROI)*

- [x] Write Alembic migration with all indexes from Section 1
- [x] Run `alembic upgrade head` on dev DB
- [x] Run `EXPLAIN ANALYZE` on discover and search queries — confirm `Index Scan` appears
- [x] Add GZip middleware to `main.py` (Section 4.1)
- [x] Add `Cache-Control` headers to 5 public endpoints (Section 4.2)
- [x] Enforce `limit` cap (le=50) on all 6 list endpoints (Section 3.6)

### ✅ Phase 2 — Redis Caching: Static Data

- [x] Create `app/core/cache.py` with all key functions and helpers (Section 2.1)
- [x] Cache `GET /api/v1/interests` (Section 2.2)
- [x] Cache `GET /api/v1/prompts?language=fa` and `?language=en` (Section 2.2)
- [x] Cache `GET /api/v1/locations/countries` (Section 2.2)
- [x] Cache `GET /api/v1/locations/provinces` (Section 2.2)
- [x] Cache `GET /api/v1/locations/cities` (Section 2.2)
- [x] Cache `GET /api/v1/subscriptions/plans` (Section 2.2)
- [x] Cache `GET /api/v1/system/status` (Section 2.3)

### ✅ Phase 3 — Redis Caching: User Data + Daily Limits

- [x] Cache `GET /api/v1/users/me` per user (Section 2.4)
- [x] Call `invalidate_user_cache()` in all 8 mutation endpoints (Section 2.4)
- [x] Cache daily limits in Redis with midnight TTL (Section 2.5)

### Phase 4 — Backend Query Optimization

- [x] Add `get_current_user_id` lightweight dependency (Section 3.1)
- [x] Switch 8 endpoints to use `get_current_user_id` (Section 3.1) — swipes excluded, needs profile.name/age
- [x] Add `selectinload` chains to `GET /discover`, `GET /search`, `GET /matches` (Section 3.2)
- [x] Move Haversine distance filter into PostgreSQL WHERE clause (Section 3.3)
- [x] Add `BackgroundTasks` to 4 endpoints — swipes + 3 message sends (Section 3.4)
- [x] Add cursor-based pagination (`before` param) to `GET /messages/{identifier}` (Section 3.5)

### Phase 5 — Flutter App

- [ ] Add `dio_cache_interceptor` + Hive store to `api_service.dart` (Section 5.1)
- [ ] Set per-endpoint cache policies (Section 5.1)
- [ ] Configure `CachedNetworkImage` with size limits everywhere (Section 5.2)
- [ ] Add `shimmer` package + `_ShimmerAvatar` placeholder (Section 5.2)
- [ ] Replace `Consumer` with `Selector` in hot-rebuild paths (Section 5.3)
- [ ] Audit all list screens — confirm `ListView.builder` + `RepaintBoundary` (Section 5.4)
- [ ] Parallelize splash screen calls with `Future.wait` (Section 5.5)
- [ ] Add WebSocket exponential backoff reconnection (Section 5.6)
- [ ] Add pagination to notifications screen (Section 5.7)

---

## Notes

- **Start with Phase 1.** Indexes require zero code changes and give the biggest query speedup.
- **Phase 3 (user profile cache) must come after Phase 2.** Cache invalidation bugs are hard to debug — get the pattern right on static data first.
- **Do not add `Cache-Control: public` to any 🔒 authenticated endpoint.**
- **`GET /discover` and `GET /search` must be `NoCache` in Flutter** — results change every time based on swipe history and user location.
- **Daily limits cache (Phase 3) is high-impact** — it turns 2 DB reads into 1 Redis read on every swipe and every message send, which are the two most frequent user actions in the app.
