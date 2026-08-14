# app/tasks/notifications.py
"""Durable notification work. When `CELERY_ENABLED=True` the request path
dispatches push/match work here (`.delay()`); the worker does the actual send
with its own DB session so it survives component restarts and retries.

⚠️ Celery tasks are sync — any async DB/Redis work (this codebase) must be
wrapped with `asyncio.run(...)` / a new loop. Do NOT run them from a running
event loop's process.
"""
import asyncio
from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger("tasks.notifications")


def dispatch_push_to_celery(*, user_id, title: str, body: str, data: dict, image_url: Optional[str] = None) -> bool:
    """Enqueue an FCM push to the worker. Returns True if dispatched, False if
    Celery is disabled (in which case the caller must send inline)."""
    if not settings.CELERY_ENABLED:
        return False
    send_push.delay(str(user_id), title, body, data, image_url)
    return True


def dispatch_announcement_push_to_celery(*, user_ids: list, title: str, body: str) -> bool:
    """Enqueue a single batched FCM push for many users (e.g. an announcement).
    Returns True if dispatched, False if Celery is disabled (caller must send
    inline via PushService.send_to_users)."""
    if not settings.CELERY_ENABLED:
        return False
    send_announcement_push.delay([str(u) for u in user_ids], title, body)
    return True


@celery_app.task(
    bind=True,
    name="app.tasks.notifications.send_push",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_push(self, user_id: str, title: str, body: str, data: dict | None = None, image_url: Optional[str] = None):
    """Send an FCM push (with retry) from the worker, using its own session."""
    async def _run():
        from app.db.session import AsyncSessionLocal
        from app.services.push_service import PushService

        async with AsyncSessionLocal() as session:
            await PushService.send_to_user(
                user_id=UUID(user_id),
                title=title,
                body=body,
                data=data or {},
                db=session,
                image_url=image_url,
            )
            # Persist dead-token cleanup performed by send_to_user.
            await session.commit()

    return asyncio.run(_run())


@celery_app.task(
    bind=True,
    name="app.tasks.notifications.send_announcement_push",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_announcement_push(self, user_ids: list, title: str, body: str):
    """Send an FCM push to many users in one batched task. Batching matters:
    a per-user broadcast (e.g. an announcement to every user) that enqueues one
    task per recipient saturates the pgbouncer pool because each task holds a
    pooled DB connection in an open transaction across the slow synchronous FCM
    round-trip. This single task releases the connection before any FCM work."""
    async def _run():
        from app.services.push_service import PushService

        await PushService.send_to_users(
            user_ids=user_ids,
            title=title,
            body=body,
            data={"type": "system", "is_announcement": True},
        )

    return asyncio.run(_run())


@celery_app.task(
    bind=True,
    name="app.tasks.notifications.send_match",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_match(self, user1_id: str, user2_id: str, match_id: str):
    """Send the match notifications + pushes (worker-owned session)."""
    async def _match():
        from app.db.session import AsyncSessionLocal
        from app.services.notification_service import NotificationService

        async with AsyncSessionLocal() as session:
            nsvc = NotificationService(session)
            await nsvc.notify_match(
                UUID(user1_id), UUID(user2_id), UUID(match_id)
            )
            await session.commit()

    return asyncio.run(_match())


__all__ = [
    "send_push",
    "send_match",
    "send_announcement_push",
    "dispatch_push_to_celery",
    "dispatch_announcement_push_to_celery",
]