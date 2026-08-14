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


BATCH_SIZE = 500


class PushService:

    @staticmethod
    async def send_to_users(
        user_ids: list,
        title: str,
        body: str,
        data: Optional[dict] = None,
        image_url: Optional[str] = None,
    ):
        """Send an FCM push to many users at once.

        Batches device tokens (FCM caps multicast at 500/call) and runs each
        synchronous FCM request in a thread. All DB work happens in a short-lived
        session that is closed *before* any FCM HTTP call, so a large campaign
        (e.g. an announcement to every user) cannot hold pooled connections open
        across slow FCM round-trips and starve pgbouncer.
        """
        if not _initialized:
            _initialize_firebase()
        if not _initialized or not user_ids:
            return

        from app.db.session import AsyncSessionLocal
        from app.models.device_token import DeviceToken

        tokens: list[str] = []
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DeviceToken.token).where(DeviceToken.user_id.in_(user_ids))
            )
            tokens = [row[0] for row in result.all()]

        if not tokens:
            return

        payload = PushService._build_data(title, body, data, image_url)
        android = messaging.AndroidConfig(priority="high")

        chunks = [
            tokens[i:i + BATCH_SIZE]
            for i in range(0, len(tokens), BATCH_SIZE)
        ]

        # Bounded concurrency: a 1000-user announcement is just ~2 chunk calls,
        # but without a cap a huge campaign would open unbounded FCM requests +
        # threadpool threads at once. 8 parallel FCM calls is plenty fast while
        # keeping memory/thread pressure flat.
        semaphore = asyncio.Semaphore(8)

        async def send_chunk(chunk: list[str]) -> tuple[int, int, list[str]]:
            async with semaphore:
                message = messaging.MulticastMessage(
                    tokens=chunk,
                    data=payload,
                    android=android,
                )
                try:
                    response = await asyncio.to_thread(
                        messaging.send_each_for_multicast, message
                    )
                except Exception:
                    logger.exception(
                        "push_batch_failed", chunk_size=len(chunk)
                    )
                    return 0, len(chunk), []
                bad = [
                    chunk[idx]
                    for idx, r in enumerate(response.responses)
                    if not r.success
                    and r.exception
                    and r.exception.code
                    in (
                        "registration-token-not-registered",
                        "invalid-registration-token",
                    )
                ]
                return response.success_count, response.failure_count, bad

        results = await asyncio.gather(*[send_chunk(c) for c in chunks])
        total_success = sum(r[0] for r in results)
        total_failure = sum(r[1] for r in results)
        invalid_tokens = [t for r in results for t in r[2]]

        logger.info(
            "push_batch_sent",
            recipient_count=len(user_ids),
            token_count=len(tokens),
            chunk_count=len(chunks),
            success_count=total_success,
            failure_count=total_failure,
        )

        if invalid_tokens:
            async with AsyncSessionLocal() as cleanup_session:
                from sqlalchemy import delete
                await cleanup_session.execute(
                    delete(DeviceToken).where(
                        DeviceToken.token.in_(invalid_tokens)
                    )
                )
                await cleanup_session.commit()
                logger.info("invalid_tokens_cleaned", count=len(invalid_tokens))

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

        # Data-only message: the app's native FirebaseMessagingService renders a
        # compact notification (small circular avatar) from `data`. Sending a
        # `notification` block makes Android draw a big expandable card instead.
        message = messaging.MulticastMessage(
            tokens=tokens,
            data=PushService._build_data(title, body, data, image_url),
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
    def _build_data(
        title: str,
        body: str,
        data: Optional[dict],
        image_url: Optional[str],
    ) -> dict:
        payload = {k: v for k, v in (data or {}).items()}
        payload.setdefault("title", title)
        payload.setdefault("body", body)
        payload.setdefault("image_url", image_url or "")
        return {k: str(v) for k, v in payload.items()}

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
