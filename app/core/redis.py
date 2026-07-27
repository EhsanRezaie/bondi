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

async def store_refresh_token(token: str, user_id: str) -> bool:
    """
    Save a refresh token → user_id mapping with 30-day TTL.
    Returns True if successful, False if Redis is unavailable.
    """
    key = f"{REFRESH_TOKEN_PREFIX}{token}"
    try:
        await redis_client.set(key, user_id, ex=REFRESH_TOKEN_TTL)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store refresh token", error=str(e))
        return False


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
        await redis_client.delete(key)
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
    pattern = f"{REFRESH_TOKEN_PREFIX}*"
    revoked_count = 0
    try:
        async for key in redis_client.scan_iter(match=pattern, count=100):
            value = await redis_client.get(key)
            if value == user_id:
                await redis_client.delete(key)
                revoked_count += 1
        return revoked_count
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to revoke all user tokens", error=str(e))
        return -1


# ============ Verification Code Functions ============

MAX_OTP_ATTEMPTS = 5


async def store_verification_code(email: str, code: str, ttl: int = VERIFICATION_CODE_TTL) -> bool:
    """
    Store verification code in Redis with attempt counter.

    Args:
        email: User's email (used as key)
        code: 6-digit verification code
        ttl: Time to live in seconds (default: 300 = 5 minutes)

    Returns:
        bool: True if stored successfully
    """
    key = f"{VERIFICATION_CODE_PREFIX}{email}"
    data = json.dumps({"code": code, "attempts": 0})
    try:
        await redis_client.set(key, data, ex=ttl)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to store verification code", error=str(e))
        return False


async def get_verification_code(email: str) -> Optional[str]:
    """
    Get raw verification code data from Redis (legacy — prefer verify_code_with_attempts).
    """
    key = f"{VERIFICATION_CODE_PREFIX}{email}"
    try:
        return await redis_client.get(key)
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to get verification code", error=str(e))
        return None


async def verify_code_with_attempts(email: str, submitted_code: str) -> bool:
    """
    Verify a code with brute-force protection. Max 5 attempts.

    Raises HTTPException on failure. Returns True on success.
    """
    key = f"{VERIFICATION_CODE_PREFIX}{email}"
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


async def delete_verification_code(email: str) -> bool:
    """
    Delete verification code from Redis.
    """
    key = f"{VERIFICATION_CODE_PREFIX}{email}"
    try:
        await redis_client.delete(key)
        return True
    except (RedisError, RedisTimeoutError) as e:
        logger.error("Failed to delete verification code", error=str(e))
        return False

async def get_redis():
    """FastAPI dependency that yields the Redis client."""
    yield redis_client


async def health_check() -> bool:
    """Check if Redis is reachable."""
    try:
        await redis_client.ping()
        return True
    except (RedisError, RedisTimeoutError, ConnectionError):
        return False