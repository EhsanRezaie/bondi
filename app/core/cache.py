import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from redis.asyncio import Redis
from sqlalchemy import Date, DateTime
from app.core.logging import get_logger
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings

logger = get_logger("core.cache")

# ── TTLs ──────────────────────────────────────────────────────────────────────
TTL_INTERESTS       = 86400      # 24h  — seed data, never changes at runtime
TTL_PROMPTS         = 86400      # 24h  — seed data, never changes at runtime
TTL_LOCATIONS       = 604800     # 7d   — countries/provinces/cities
TTL_SYSTEM_STATUS   = 60         # 60s  — /system/status
TTL_SUB_PLANS       = 3600       # 1h   — /subscriptions/plans
TTL_USER_PROFILE    = 600        # 10m  — /users/me
TTL_USER_PHOTOS     = 600        # 10m  — /users/me/photos
TTL_PUBLIC_PROFILE  = 300        # 5m   — GET /users/{user_id} (public profile)
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

def key_public_profile(user_id: UUID) -> str:
    return f"cache:user:{user_id}:public_profile"

def key_daily_limits(user_id: UUID, date: str) -> str:
    return f"cache:limits:{user_id}:{date}"


# ── Helpers ───────────────────────────────────────────────────────────────────
async def cache_get(redis: Redis, key: str):
    """Get and deserialize a cached value. Returns None on miss."""
    try:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("cache_get_failed", key=key, error=str(e), exc_info=True)
        return None


async def cache_set(redis: Redis, key: str, value, ttl: int):
    """Serialize and store a value with TTL."""
    try:
        await redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        logger.warning("cache_set_failed", key=key, error=str(e), exc_info=True)


async def invalidate_user_cache(redis: Redis, user_id: UUID):
    """Invalidate all cached data for a user."""
    keys = [
        key_user_profile(user_id),
        key_user_photos(user_id),
        key_public_profile(user_id),
    ]
    try:
        await redis.delete(*keys)
    except Exception as e:
        logger.warning("cache_invalidate_failed", keys=keys, error=str(e), exc_info=True)


# ── Discover Card Stack ──────────────────────────────────────────────────────
DISCOVER_STACK_TTL = 1800    # 30 minutes
DISCOVER_STACK_SIZE = 50     # pre-fetch 50 cards at a time
SWIPED_SET_TTL = 7 * 86400   # 7 days


def key_discover_stack(user_id: UUID) -> str:
    return f"cache:discover:{user_id}:stack"


async def pop_discover_stack(redis: Redis, user_id: UUID, count: int) -> list[str]:
    """Pop `count` user IDs from the cached discover stack."""
    key = key_discover_stack(user_id)
    try:
        pipe = redis.pipeline()
        pipe.lrange(key, 0, count - 1)
        pipe.ltrim(key, count, -1)
        results = await pipe.execute()
        return results[0]
    except Exception as e:
        logger.warning("discover_stack_pop_failed", key=key, error=str(e), exc_info=True)
        return []


async def set_discover_stack(redis: Redis, user_id: UUID, user_ids: list[str]):
    """Replace the discover stack with a new list of user IDs."""
    key = key_discover_stack(user_id)
    try:
        await redis.delete(key)
        if user_ids:
            await redis.lpush(key, *reversed(user_ids))
            await redis.expire(key, DISCOVER_STACK_TTL)
    except Exception as e:
        logger.warning("discover_stack_set_failed", key=key, error=str(e), exc_info=True)


async def invalidate_discover_stack(redis: Redis, user_id: UUID):
    """Invalidate discover stack (e.g. on location update)."""
    try:
        await redis.delete(key_discover_stack(user_id))
    except Exception as e:
        logger.warning("discover_stack_invalidate_failed", user_id=str(user_id), error=str(e), exc_info=True)


# ── Swipe Deduplication ──────────────────────────────────────────────────────

async def record_swipe_cache(redis: Redis, swiper_id: UUID, swipee_id: UUID):
    """Add a swipe to the Redis set for fast exclusion in discover."""
    key = f"swiped:{swiper_id}"
    try:
        await redis.sadd(key, str(swipee_id))
        await redis.expire(key, SWIPED_SET_TTL)
    except Exception as e:
        logger.warning("record_swipe_cache_failed", swiper_id=str(swiper_id), error=str(e), exc_info=True)


async def get_swiped_ids(redis: Redis, user_id: UUID) -> set[str]:
    """Get all swiped user IDs from Redis set."""
    key = f"swiped:{user_id}"
    try:
        members = await redis.smembers(key)
        return {m for m in members}
    except Exception as e:
        logger.warning("get_swiped_ids_failed", user_id=str(user_id), error=str(e), exc_info=True)
        return set()


# ── Auth User Snapshot (P0-2) ─────────────────────────────────────────────────
# get_current_user hits Redis first (keyed by user_id + token_version), falling
# back to the DB on miss/error. Snapshots carry all User/UserProfile/UserSettings
# columns so read endpoints behave identically; computed properties (is_premium,
# age, is_profile_complete) are re-derived on read.

TTL_AUTH_USER = 30  # seconds


def key_auth_user(user_id: UUID, token_version: int) -> str:
    return f"cache:auth:{user_id}:v{token_version}"


def _row_to_dict(obj) -> dict | None:
    if obj is None:
        return None
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _dict_to_obj(model, data: dict) -> SimpleNamespace | None:
    if not data:
        return None
    attrs = {}
    for c in model.__table__.columns:
        val = data.get(c.name)
        if val is not None:
            if isinstance(c.type, DateTime):
                val = datetime.fromisoformat(val)
            elif isinstance(c.type, Date):
                val = date.fromisoformat(val)
        attrs[c.name] = val
    return SimpleNamespace(**attrs)


def _profile_from_cache(data: dict) -> SimpleNamespace | None:
    p = _dict_to_obj(UserProfile, data)
    if p is None:
        return None
    now = datetime.now(timezone.utc)
    p.is_premium = p.premium_until is not None and p.premium_until > now
    if p.birth_date is None:
        p.age = 0
    else:
        today = date.today()
        age = today.year - p.birth_date.year
        if (today.month, today.day) < (p.birth_date.month, p.birth_date.day):
            age -= 1
        p.age = age
    p.is_profile_complete = all([
        p.name is not None,
        p.birth_date is not None,
        p.gender is not None,
        p.lat is not None,
        p.lng is not None,
    ])
    return p


def _user_from_cache(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(data["id"]),
        is_active=data["is_active"],
        token_version=data["token_version"],
        registration_status=data.get("registration_status"),
        referral_code=data.get("referral_code"),
        phone=data.get("phone"),
        email=data.get("email"),
        profile=_profile_from_cache(data.get("profile")),
        settings=_dict_to_obj(UserSettings, data.get("settings")),
    )


async def cache_auth_user(redis: Redis, user, token_version: int) -> None:
    """Serialize the DB-loaded user (with profile/settings attached) into Redis."""
    key = key_auth_user(user.id, token_version)
    data = {
        "id": str(user.id),
        "is_active": user.is_active,
        "token_version": user.token_version,
        "registration_status": user.registration_status,
        "referral_code": user.referral_code,
        "phone": user.phone,
        "email": user.email,
        "profile": _row_to_dict(user.profile),
        "settings": _row_to_dict(user.settings),
    }
    try:
        await redis.set(key, json.dumps(data, default=str), ex=TTL_AUTH_USER)
    except Exception as e:
        logger.warning("cache_auth_user_failed", key=key, error=str(e))


async def get_cached_auth_user(redis: Redis, user_id, token_version: int):
    """Return a lightweight snapshot or None on miss/error."""
    key = key_auth_user(user_id, token_version)
    try:
        raw = await redis.get(key)
    except Exception as e:
        logger.warning("cache_get_auth_user_failed", key=key, error=str(e))
        return None
    if not raw:
        return None
    try:
        return _user_from_cache(json.loads(raw))
    except Exception as e:
        logger.warning("cache_auth_user_parse_failed", key=key, error=str(e))
        return None


async def invalidate_auth_user(redis: Redis, user_id: UUID) -> None:
    """Clear all cached auth snapshots for a user (all token versions)."""
    try:
        async for k in redis.scan_iter(match=f"cache:auth:{user_id}:v*"):
            await redis.delete(k)
    except Exception as e:
        logger.warning("cache_invalidate_auth_user_failed", user_id=str(user_id), error=str(e))
