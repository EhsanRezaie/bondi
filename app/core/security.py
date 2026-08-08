from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("core.security")

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plain-text password."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored hash."""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

REFRESH_TOKEN_EXPIRE_DAYS = 30


def _create_token(subject: str, token_type: str, expires_delta: timedelta, token_version: int = 1) -> str:
    """Internal helper — build and sign a JWT with unique jti and version."""
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": subject,
        "type": token_type,
        "exp": expire,
        "jti": secrets.token_urlsafe(16),
        "iat": datetime.now(timezone.utc),
        "ver": token_version,  # Token version for revocation
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: str, token_version: int = 1) -> str:
    """Return a signed access token valid for ACCESS_TOKEN_EXPIRE_MINUTES."""
    return _create_token(
        subject=user_id,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_version=token_version,
    )


def create_refresh_token(user_id: str, token_version: int = 1) -> str:
    """
    Return a signed refresh token.
    NOTE: caller must also call redis.store_refresh_token() to make it valid.
    """
    return _create_token(
        subject=user_id,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        token_version=token_version,
    )


def decode_token(token: str, expected_type: str) -> Optional[dict]:
    """
    Decode and validate JWT signature + expiry + type.
    Returns full payload dict or None.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except JWTError as e:
        logger.warning("jwt_decode_failed", token_type=expected_type, error=str(e))
        return None


def decode_access_token(token: str) -> Optional[str]:
    """Returns user_id if valid, None otherwise."""
    payload = decode_token(token, ACCESS_TOKEN_TYPE)
    return payload.get("sub") if payload else None


def get_token_version(token: str, expected_type: str) -> Optional[int]:
    """Extract token version from JWT."""
    payload = decode_token(token, expected_type)
    return payload.get("ver") if payload else None


def decode_refresh_token(token: str) -> Optional[str]:
    """Returns user_id if valid, None otherwise."""
    payload = decode_token(token, REFRESH_TOKEN_TYPE)
    return payload.get("sub") if payload else None


# ---------------------------------------------------------------------------
# Admin JWT
# ---------------------------------------------------------------------------

ADMIN_TOKEN_TYPE = "admin"
ADMIN_TOKEN_EXPIRE_MINUTES = 60


def create_admin_token(admin_id: str) -> str:
    """Create a short-lived admin JWT token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ADMIN_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": admin_id,
        "type": ADMIN_TOKEN_TYPE,
        "role": "admin",
        "exp": expire,
        "jti": secrets.token_urlsafe(16),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.ADMIN_SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_admin_token(token: str) -> Optional[dict]:
    """Decode and validate admin JWT. Returns payload or None."""
    try:
        payload = jwt.decode(token, settings.ADMIN_SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("role") != "admin":
            return None
        return payload
    except JWTError as e:
        logger.warning("admin_jwt_decode_failed", error=str(e))
        return None