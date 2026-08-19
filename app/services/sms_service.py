"""
SMS service for sending verification codes via Kavenegar through an OAuth2 API gateway.

Flow:
  1. Mint an OAuth2 client_credentials token from SMS_TOKEN_URL (cached in Redis).
  2. POST {SMS_BASE_URL}/send-sms with Bearer token and form body
     {message, receptor}.

When SMS is not enabled (SMS_ENABLED=false or creds missing) the code is just
logged/printed — matching the old email_service behaviour — so local dev works
without an SMS account.
"""
from typing import Optional

import httpx

import app.core.redis as redis_module
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("services.sms_service")

SMS_TOKEN_CACHE_KEY = "sms_access_token"

OTP_MESSAGE_TEMPLATE = "کد تایید باندی: {code}"


async def _get_token() -> Optional[str]:
    """Return a cached OAuth2 access token, minting a new one if needed."""
    cached = await redis_module.redis_client.get(SMS_TOKEN_CACHE_KEY)
    if cached:
        return cached

    if not settings.SMS_CLIENT_ID or not settings.SMS_CLIENT_SECRET:
        logger.warning("sms_client_credentials_missing")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.SMS_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(settings.SMS_CLIENT_ID, settings.SMS_CLIENT_SECRET),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("sms_token_request_failed", error=str(e), exc_info=True)
        return None

    data = response.json()
    token = data.get("access_token")
    if not token:
        logger.error("sms_token_response_missing_access_token")
        return None

    expires_in = int(data.get("expires_in", 3600))
    ttl = max(expires_in - 60, 60)
    try:
        await redis_module.redis_client.set(SMS_TOKEN_CACHE_KEY, token, ex=ttl)
    except Exception as e:
        logger.warning("sms_token_cache_failed", error=str(e), exc_info=True)

    return token


def normalize_receptor(phone: str) -> str:
    """Convert E.164 ('+989379191281') to the gateway's digit format (989379191281)."""
    return phone.lstrip("+")


async def send_verification_code(phone: str, code: str) -> bool:
    """
    Send a 6-digit verification code to `phone`.

    Returns True if the SMS was dispatched (or logged in dev mode).
    """
    receptor = normalize_receptor(phone)
    message = OTP_MESSAGE_TEMPLATE.format(code=code)

    if not settings.SMS_ENABLED:
        logger.info("sms_otp_dev_mode", phone=phone, code=code)
        return True

    if not settings.SMS_CLIENT_ID or not settings.SMS_CLIENT_SECRET:
        logger.info("sms_otp_no_credentials", phone=phone, code=code)
        return True

    token = await _get_token()
    if not token:
        logger.error("sms_send_failed_no_token", phone=phone)
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{settings.SMS_BASE_URL.rstrip('/')}/send-sms",
                data={
                    "message": message,
                    "receptor": receptor,
                    **({"sender": settings.SMS_SENDER_LINE} if settings.SMS_SENDER_LINE else {}),
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("sms_send_failed", phone=phone, error=str(e), exc_info=True)
        return False

    logger.info("sms_otp_sent", phone=phone)
    return True
