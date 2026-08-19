from typing import Optional
import json
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from app.core.config import settings
from app.core.logging import get_logger

from fastapi import HTTPException, status

logger = get_logger("core.redis")

# Production Redis client with retries and timeouts
redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
    retry=Retry(ExponentialBackoff(), settings.REDIS_MAX_RETRIES),
    retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
)

REFRESH_TOKEN_PREFIX = "refresh_token:"
USER_TOKENS_PREFIX = "user_tokens:"
TOKEN_FAMILY_PREFIX = "token_family:"      # token -> family id
AUTH_FAMILY_PREFIX = "auth_family:"        # family id -> set of tokens
ROTATED_TOKEN_PREFIX = "rotated_token:"
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days in seconds

VERIFICATION_CODE_PREFIX = "verification:"
VERIFICATION_CODE_TTL = 300  # 5 minutes in seconds


async def _safe_redis_operation(operation, fallback=None):
    """Wrapper for safe Redis operations with logging."""
    try:
        return await operation
    except (RedisError, RedisTimeoutError, ConnectionError) as e:
        logger.error("Redis operation failed", error=str(e))
        if fallback is not None:
            return fallback
        raise


# ============ Refresh Token Functions ============

async def store_refresh_token(token: str, user_id: str, family_id: Optional[str] = None) -> str:
    """
    Save a refresh token → user_id mapping with 30-day TTL.
    Also tracks the token in a per-user set so revoke_all_user_tokens is O(tokens),
    and in a rotation family for theft detection.

    Args:
        token: The refresh token string.
        user_id: Owning user.
        family_id: Rotation family to join. If None, the token starts its own family.

    Returns:
        The family_id the token now belongs to ("" - empty string on failure).
    """
    key = f"{REFRESH_TOKEN_PREFIX}{token}"
    member_key = f"{USER_TOKENS_PREFIX}{user_id}"
    fam = family_id or token
    fam_key = f"{TOKEN_FAMILY_PREFIX}{token}"
    family_set = f"{AUTH_FAMILY_PREFIX}{fam}"
    try:
        async with redis_client.pipeline() as pipe:
            pipe.set(key, user_id, ex=REFRESH_TOKEN_TTL)
            pipe.sadd(member_key, token)
            pipe.expire(member_key, REFRESH_TOKEN_TTL)
            pipe.set(fam_key, fam)
            pipe.expire(fam_key, REFRESH_TOKEN_TTL)
            pipe.sadd(family_set, token)
            pipe.expire(family_set, REFRESH_TOKEN_TTL)
            await pipe.execute()
        return fam
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store refresh token", error=str(e))
        return ""


async def get_refresh_token_owner(token: str) -> Optional[str]:
    """
    Return the user_id that owns this refresh token, or None if missing/expired.
    Returns None if Redis is unavailable.
    """
    key = f"{REFRESH_TOKEN_PREFIX}{token}"
    try:
        return await redis_client.get(key)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get refresh token owner", error=str(e))
        return None


async def revoke_refresh_token(token: str) -> bool:
    """
    Delete a refresh token — used on logout, rotation, and ban.
    Returns True if deleted or already gone, False if Redis error.
    """
    key = f"{REFRESH_TOKEN_PREFIX}{token}"
    try:
        user_id = await redis_client.get(key)
        fam = await redis_client.get(f"{TOKEN_FAMILY_PREFIX}{token}")
        async with redis_client.pipeline() as pipe:
            pipe.delete(key)
            if user_id:
                pipe.srem(f"{USER_TOKENS_PREFIX}{user_id}", token)
            if fam:
                pipe.srem(f"{AUTH_FAMILY_PREFIX}{fam}", token)
                pipe.delete(f"{TOKEN_FAMILY_PREFIX}{token}")
            await pipe.execute()
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to revoke refresh token", error=str(e))
        return False


async def revoke_all_user_tokens(user_id: str) -> int:
    """
    Revoke ALL refresh tokens for a user.
    Used when password changes or account is banned.
    Returns number of tokens revoked, -1 on error.
    """
    member_key = f"{USER_TOKENS_PREFIX}{user_id}"
    revoked_count = 0
    try:
        token_ids = await redis_client.smembers(member_key)
        if token_ids:
            async with redis_client.pipeline() as pipe:
                for token in token_ids:
                    pipe.delete(f"{REFRESH_TOKEN_PREFIX}{token}")
                pipe.delete(member_key)
                await pipe.execute()
            revoked_count = len(token_ids)
        else:
            await redis_client.delete(member_key)
        return revoked_count
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to revoke all user tokens", error=str(e))
        return -1


async def get_token_family(token: str) -> Optional[str]:
    """
    Return the rotation-family id a token belongs to, or None.
    Used on refresh to keep a token in its existing family.
    """
    try:
        return await redis_client.get(f"{TOKEN_FAMILY_PREFIX}{token}")
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get token family", error=str(e))
        return None


async def is_rotated_token(token: str) -> bool:
    """
    True if this token was already rotated (i.e. is being reused) —
    the OAuth2 refreshed-token-reuse theft signal.
    """
    try:
        return bool(await redis_client.exists(f"{ROTATED_TOKEN_PREFIX}{token}"))
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to check rotated token", error=str(e))
        return False


async def mark_token_rotated(token: str, family_id: str) -> bool:
    """
    Mark a token as already-rotated so presenting it again triggers family revocation.
    """
    try:
        await redis_client.set(
            f"{ROTATED_TOKEN_PREFIX}{token}", family_id, ex=REFRESH_TOKEN_TTL
        )
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to mark token rotated", error=str(e))
        return False


async def revoke_refresh_family(family_id: str) -> int:
    """
    Revoke ENTIRE rotation family (forced re-login everywhere).
    Used when a rotated token is replayed. Returns number of tokens revoked.
    """
    family_set = f"{AUTH_FAMILY_PREFIX}{family_id}"
    revoked_count = 0
    try:
        tokens = await redis_client.smembers(family_set)
        async with redis_client.pipeline() as pipe:
            for token in tokens:
                pipe.delete(f"{REFRESH_TOKEN_PREFIX}{token}")
                pipe.delete(f"{TOKEN_FAMILY_PREFIX}{token}")
                pipe.delete(f"{ROTATED_TOKEN_PREFIX}{token}")
            pipe.delete(family_set)
            await pipe.execute()
        return len(tokens)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to revoke refresh family", error=str(e))
        return -1


# ============ Verification Code Functions ============

MAX_OTP_ATTEMPTS = 5

OTP_COOLDOWN_PREFIX = "otp_cooldown:"
OTP_COOLDOWN_TTL = 60  # seconds between resend requests


# ============ Face Reference (identity anchor) ============

FACE_REFERENCE_PREFIX = "face_ref:"
FACE_REFERENCE_TTL = 60 * 60 * 24 * 7  # 7 days


async def store_face_reference(user_id: str, embedding: list, ttl: int = FACE_REFERENCE_TTL) -> bool:
    """Cache the canonical face embedding for a user (selfie anchor)."""
    key = f"{FACE_REFERENCE_PREFIX}{user_id}"
    try:
        await redis_client.set(key, json.dumps(embedding), ex=ttl)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store face reference", error=str(e))
        return False


async def get_face_reference(user_id: str) -> Optional[list]:
    """Return the cached face embedding (list of floats) or None."""
    key = f"{FACE_REFERENCE_PREFIX}{user_id}"
    try:
        raw = await redis_client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (RedisError, RedisTimeoutError, json.JSONDecodeError) as e:
        logger.error("Failed to get face reference", error=str(e))
        return None


async def delete_face_reference(user_id: str) -> bool:
    """Remove the cached face reference for a user."""
    key = f"{FACE_REFERENCE_PREFIX}{user_id}"
    try:
        await redis_client.delete(key)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to delete face reference", error=str(e))
        return False


async def store_verification_code(identifier: str, code: str, ttl: int = VERIFICATION_CODE_TTL) -> bool:
    """
    Store verification code in Redis with attempt counter.

    Args:
        identifier: User's phone (or email) used as key
        code: 6-digit verification code
        ttl: Time to live in seconds (default: 300 = 5 minutes)

    Returns:
        bool: True if stored successfully
    """
    key = f"{VERIFICATION_CODE_PREFIX}{identifier}"
    data = json.dumps({"code": code, "attempts": 0})
    try:
        await redis_client.set(key, data, ex=ttl)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store verification code", error=str(e))
        return False


async def get_verification_code(identifier: str) -> Optional[str]:
    """
    Get raw verification code data from Redis (legacy — prefer verify_code_with_attempts).
    """
    key = f"{VERIFICATION_CODE_PREFIX}{identifier}"
    try:
        return await redis_client.get(key)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get verification code", error=str(e))
        return None


async def verify_code_with_attempts(identifier: str, submitted_code: str) -> bool:
    """
    Verify a code with brute-force protection. Max 5 attempts.

    Raises HTTPException on failure. Returns True on success.
    """
    key = f"{VERIFICATION_CODE_PREFIX}{identifier}"
    try:
        raw = await redis_client.get(key)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get verification code", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error.",
        )

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    # Backward compatibility: plain string (old format from tests)
    if not raw.startswith("{"):
        # Old format — plain code string, no attempt tracking
        if raw == submitted_code:
            await redis_client.delete(key)
            return True
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    data = json.loads(raw)

    if data["attempts"] >= MAX_OTP_ATTEMPTS:
        await redis_client.delete(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Request a new code.",
        )

    if data["code"] != submitted_code:
        data["attempts"] += 1
        ttl = await redis_client.ttl(key)
        if ttl > 0:
            await redis_client.set(key, json.dumps(data), ex=ttl)
        remaining = MAX_OTP_ATTEMPTS - data["attempts"]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid code. {remaining} attempt{'s' if remaining != 1 else ''} left.",
        )

    # Success — delete the code (single-use)
    await redis_client.delete(key)
    return True


async def delete_verification_code(identifier: str) -> bool:
    """
    Delete verification code from Redis.
    """
    key = f"{VERIFICATION_CODE_PREFIX}{identifier}"
    try:
        await redis_client.delete(key)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to delete verification code", error=str(e))
        return False


# ============ OTP Resend Cooldown Functions ============


async def store_otp_cooldown(identifier: str, ttl: int = OTP_COOLDOWN_TTL) -> bool:
    """Set a resend cooldown marker for the identifier (e.g. phone)."""
    key = f"{OTP_COOLDOWN_PREFIX}{identifier}"
    try:
        await redis_client.set(key, "1", ex=ttl)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store OTP cooldown", error=str(e))
        return False


async def is_in_otp_cooldown(identifier: str) -> bool:
    """True if the identifier is still in resend cooldown."""
    key = f"{OTP_COOLDOWN_PREFIX}{identifier}"
    try:
        return bool(await redis_client.exists(key))
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to check OTP cooldown", error=str(e))
        return False


async def get_otp_cooldown(identifier: str) -> int:
    """Return remaining cooldown seconds (0 if not in cooldown)."""
    key = f"{OTP_COOLDOWN_PREFIX}{identifier}"
    try:
        ttl = await redis_client.ttl(key)
        return max(ttl, 0)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get OTP cooldown", error=str(e))
        return 0


async def get_redis():
    """FastAPI dependency that yields the Redis client."""
    yield redis_client


async def health_check() -> bool:
    """Check if Redis is reachable."""
    try:
        await redis_client.ping()
        return True
    except (RedisError, RedisTimeoutError, ConnectionError) as e:
        logger.warning("redis_health_check_failed", error=str(e), exc_info=True)
        return False