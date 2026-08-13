import asyncio

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("push_service")

_initialized = False


def _initialize_firebase():
    global _initialized
    if _initialized:
        return
    if not settings.FCM_SERVICE_ACCOUNT_PATH:
        logger.warning("FCM_SERVICE_ACCOUNT_PATH not configured, push notifications disabled")
        return
    try:
        cred = credentials.Certificate(settings.FCM_SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred)
        _initialized = True
        logger.info("firebase_initialized")
    except FileNotFoundError:
        logger.warning(
            "FCM service account file not found at %s — push notifications disabled",
            settings.FCM_SERVICE_ACCOUNT_PATH,
        )
    except Exception:
        logger.exception("firebase_init_failed")


class PushService:

    @staticmethod
    async def send_to_user(
        user_id: UUID,
        title: str,
        body: str,
        data: Optional[dict] = None,
        db: Optional[AsyncSession] = None,
        image_url: Optional[str] = None,
    ):
        if not _initialized:
            _initialize_firebase()
        if not _initialized:
            return

        if not db:
            return

        tokens = await PushService._get_user_tokens(user_id, db)
        if not tokens:
            return

        notification = messaging.Notification(title=title, body=body)
        if image_url:
            notification.image = image_url

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=notification,
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(priority="high"),
        )

        try:
            # messaging.send_each_for_multicast() is a synchronous HTTP call to
            # Google FCM — run it in a thread so it never blocks the event loop
            # (a burst of matches/likes would otherwise stall every connection
            # on this worker for the duration of each FCM round-trip).
            response = await asyncio.to_thread(messaging.send_each_for_multicast, message)
            logger.info(
                "push_sent",
                user_id=str(user_id),
                success_count=response.success_count,
                failure_count=response.failure_count,
            )
            await PushService._cleanup_invalid_tokens(tokens, response, db)
        except Exception:
            logger.exception("push_send_failed", user_id=str(user_id))

    @staticmethod
    async def _get_user_tokens(user_id: UUID, db: AsyncSession) -> list[str]:
        from app.models.device_token import DeviceToken
        result = await db.execute(
            select(DeviceToken.token).where(DeviceToken.user_id == user_id)
        )
        return [row[0] for row in result.all()]

    @staticmethod
    async def _cleanup_invalid_tokens(
        tokens: list[str], response: messaging.BatchResponse, db: AsyncSession
    ):
        from app.models.device_token import DeviceToken

        invalid_tokens = []
        for idx, send_response in enumerate(response.responses):
            if not send_response.success:
                error = send_response.exception
                if error and error.code in (
                    "registration-token-not-registered",
                    "invalid-registration-token",
                ):
                    invalid_tokens.append(tokens[idx])

        if invalid_tokens:
            await db.execute(
                select(DeviceToken).where(DeviceToken.token.in_(invalid_tokens))
            )
            # Delete invalid tokens
            from sqlalchemy import delete
            await db.execute(
                delete(DeviceToken).where(DeviceToken.token.in_(invalid_tokens))
            )
            await db.flush()
            logger.info("invalid_tokens_cleaned", count=len(invalid_tokens))
