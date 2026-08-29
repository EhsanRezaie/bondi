# app/tasks/cleanup.py
"""Scheduled maintenance tasks (run by celery-beat → celery-worker).

Each task reuses existing async services/Redis helpers and opens its own
session in the worker process (`asyncio.run` in the task body).
"""
import asyncio
from datetime import timedelta

from app.core.config import settings
from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger("tasks.cleanup")


@celery_app.task(
    bind=True,
    name="app.tasks.cleanup.expire_lapsed_premium",
)
def expire_lapsed_premium(self):
    """Sweep profiles whose premium_until has passed. `is_premium` is a derived
    property (premium_until > now), so this is a safety net that optionally
    drops the row's premium marker if we ever add one. Logs counts only."""
    async def _sweep():
        from sqlalchemy import select, func
        from app.db.session import AsyncSessionLocal
        from app.models.user_profile import UserProfile

        async with AsyncSessionLocal() as session:
            total = (await session.execute(select(func.count()).select_from(UserProfile)))
            lapsed = await session.execute(
                select(func.count()).select_from(UserProfile).where(
                    UserProfile.premium_until.is_not(None),
                    UserProfile.premium_until < func.now(),
                )
            )
            return {"profiles": total.scalar_one(), "lapsed_premium": lapsed.scalar_one()}

    try:
        return asyncio.run(_sweep())
    except Exception:
        logger.exception("premium_sweep_failed")
        return None


@celery_app.task(
    bind=True,
    name="app.tasks.cleanup.cleanup_stale_onlines",
)
def cleanup_stale_onlines(self):
    """Best-effort cleanup of stale `online:*` keys left by crashed workers.
    NOTE: keys carry a 60s TTL (`ONLINE_TTL`), so this normally clears nothing;
    kept as an idempotent safety net for key bloat."""
    async def _clean():
        import app.core.redis as redis_local
        redis = redis_local.redis_client
        async for key in redis.scan_iter("online:*", count=1000):
            await redis.delete(key)
        return "ok"

    try:
        return asyncio.run(_clean())
    except Exception:
        logger.exception("stale_online_cleanup_failed")
        return None


async def _purge_deleted_accounts(session_factory=None) -> dict:
    """Async core of the purge sweep — testable without the Celery wrapper.

    `session_factory` is injectable so tests can pass a factory bound to their
    own event-loop-local engine (the global AsyncSessionLocal's engine is created
    at import time, which mismatches pytest-asyncio's per-test event loops).
    """
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.user import User
    from app.models.photo import Photo
    from app.services.photo_service import PhotoService
    from app.core.timezone import utcnow

    session_factory = session_factory or AsyncSessionLocal
    cutoff = utcnow() - timedelta(days=settings.DELETE_ACCOUNT_GRACE_DAYS)
    purged = 0
    async with session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.is_active.is_(False),
                User.deleted_at.is_not(None),
                User.deleted_at < cutoff,
            ).limit(100)
        )
        for user in result.scalars().all():
            photos = await session.execute(select(Photo).where(Photo.user_id == user.id))
            for photo in photos.scalars().all():
                try:
                    await PhotoService.delete_photo(str(user.id), str(photo.id))
                except Exception:
                    logger.exception(
                        "purge_photo_delete_failed",
                        user_id=str(user.id),
                        photo_id=str(photo.id),
                    )
            await session.delete(user)
            purged += 1
        await session.commit()
        return {"purged": purged}


@celery_app.task(
    bind=True,
    name="app.tasks.cleanup.purge_deleted_accounts",
)
def purge_deleted_accounts(self):
    """Hard-delete accounts whose grace period (DELETE_ACCOUNT_GRACE_DAYS) has
    fully elapsed since the delete request. MinIO photo objects are removed
    first, then the user row is deleted — DB-level ON DELETE CASCADE clears all
    related data (profile, settings, photos, swipes, matches, chats, messages,
    notifications, reports, tickets, blocks, subscriptions, interests, prompts,
    device tokens, etc.), freeing the phone for a fresh signup."""
    try:
        return asyncio.run(_purge_deleted_accounts())
    except Exception:
        logger.exception("purge_deleted_accounts_failed")
        return None


@celery_app.task(
    bind=True,
    name="app.tasks.cleanup.inactive_user_nudge",
)
def inactive_user_nudge(self):
    """Send a 'we miss you' push to users who haven't been seen for ≥ 7 days
    (only fires when FCM is configured; enqueues nothing if disabled)."""
    async def _nudge():
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.user import User
        from app.services.push_service import PushService
        from app.core.timezone import utcnow

        cutoff = utcnow() - timedelta(days=7)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.is_active.is_(True),
                    (User.last_seen_at.is_(None)) | (User.last_seen_at < cutoff)
                ).limit(500)
            )
            users = result.scalars().all()
            for u in users:
                await PushService.send_to_user(
                    user_id=u.id,
                    title="We miss you!",
                    body="Bondi has new people waiting. Come say hi!",
                    data={"type": "inactive_nudge"},
                    db=session,
                )
            return {"nudged": len(users)}

    if not settings.FCM_SERVICE_ACCOUNT_PATH:
        return {"nudged": 0, "skipped": "no_fcm_configured"}
    try:
        return asyncio.run(_nudge())
    except Exception:
        logger.exception("inactive_nudge_failed")
        return None


__all__ = ["expire_lapsed_premium", "cleanup_stale_onlines", "purge_deleted_accounts", "inactive_user_nudge"]