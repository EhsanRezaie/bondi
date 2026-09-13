"""
SMS service for sending verification codes via the Kavenegar SMS gateway
(WSO2 APIM front, internal "ahuryx" gateway).

Flow:
  1. Mint an OAuth2 client_credentials token from SMS_TOKEN_URL (cached in Redis).
  2. POST {SMS_BASE_URL}/send-sms with the Bearer token and form body
     {message, receptor}.

When SMS is not enabled (SMS_ENABLED=false) the code is only logged/printed so
local dev and tests keep working without an SMS account.
"""
from typing import Optional

import httpx

import app.core.redis as redis_module
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("services.sms_service")

SMS_TOKEN_CACHE_KEY = "sms_access_token"

OTP_MESSAGE_TEMPLATES = {
    "fa": "کد تایید باندی: {code}",
    "en": "Bondi verification code: {code}",
}


async def _get_token() -> Optional[str]:
    """Return a cached OAuth2 access token, minting a new one if needed."""
    cached = await redis_module.redis_client.get(SMS_TOKEN_CACHE_KEY)
    if cached:
        return cached

    if not settings.SMS_CLIENT_ID or not settings.SMS_CLIENT_SECRET:
        logger.warning("sms_client_credentials_missing")
        return None

    try:
        async with httpx.AsyncClient(timeout=10, verify=settings.SMS_VERIFY_SSL) as client:
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
    """Convert an E.164 phone ('+989381072001') to national format ('09381072001')."""
    digits = phone.lstrip("+").replace(" ", "")
    if digits.startswith("98") and len(digits) == 12:
        return "0" + digits[2:]
    return digits


async def send_verification_code(phone: str, code: str, language: str = "fa") -> bool:
    """
    Send a 6-digit verification code to `phone` through the Kavenegar gateway.

    Returns True if the SMS was dispatched (or logged in dev mode).
    """
    receptor = normalize_receptor(phone)
    template = OTP_MESSAGE_TEMPLATES.get(language, OTP_MESSAGE_TEMPLATES["fa"])
    message = template.format(code=code)

    if not settings.SMS_ENABLED:
        logger.info("sms_otp_dev_mode", phone=phone, code=code)
        return True

    if not settings.SMS_CLIENT_ID or not settings.SMS_CLIENT_SECRET:
        logger.warning("sms_otp_no_credentials", phone=phone)
        return False

    token = await _get_token()
    if not token:
        logger.error("sms_send_failed_no_token", phone=phone)
        return False

    try:
        async with httpx.AsyncClient(timeout=10, verify=settings.SMS_VERIFY_SSL) as client:
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
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("sms_send_failed", phone=phone, error=str(e), exc_info=True)
        return False

    logger.info("sms_otp_sent", phone=phone, code=code)
    return True
