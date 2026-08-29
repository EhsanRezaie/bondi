# app/tasks/celery_app.py
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "bondi",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.notifications",
        "app.tasks.cleanup",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="bondi",
    broker_transport_options={"visibility_timeout": 3600},
    timezone="Asia/Tehran",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "premium-expiry-sweep-daily": {
            "task": "app.tasks.cleanup.expire_lapsed_premium",
            "schedule": 3600.0 * 6,  # every 6h — cheap idempotent sweep
        },
        "stale-session-cleanup-daily": {
            "task": "app.tasks.cleanup.cleanup_stale_onlines",
            "schedule": 3600.0 * 12,  # every 12h
        },
        "inactive-nudge-daily": {
            "task": "app.tasks.cleanup.inactive_user_nudge",
            "schedule": 3600.0 * 24,
        },
        "purge-deleted-accounts-daily": {
            "task": "app.tasks.cleanup.purge_deleted_accounts",
            "schedule": 3600.0 * 24,
        },
    },
)

__all__ = ["celery_app"]