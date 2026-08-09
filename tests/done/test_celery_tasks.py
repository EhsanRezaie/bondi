# tests/done/test_celery_tasks.py
import pytest

import app.tasks.notifications  # noqa: F401  (registers tasks)
import app.tasks.cleanup  # noqa: F401  (registers tasks)
from app.tasks.celery_app import celery_app


class TestCeleryRegistration:

    def test_queue_task_registered(self):
        names = set(celery_app.tasks.keys())
        assert "app.tasks.notifications.send_push" in names
        assert "app.tasks.notifications.send_match" in names
        assert "app.tasks.cleanup.expire_lapsed_premium" in names
        assert "app.tasks.cleanup.cleanup_stale_onlines" in names
        assert "app.tasks.cleanup.inactive_user_nudge" in names

    def test_beat_schedule_set(self):
        schedule = celery_app.conf.beat_schedule
        assert "premium-expiry-sweep-daily" in schedule
        assert "stale-session-cleanup-daily" in schedule
        assert "inactive-nudge-daily" in schedule

    def test_push_task_autoretry_config(self):
        task = celery_app.tasks["app.tasks.notifications.send_push"]
        assert getattr(task, "max_retries", None) == 3
        assert getattr(task, "autoretry_for", None) == (Exception,)

    def test_dispatch_disabled_runs_inline(self):
        from app.tasks.notifications import dispatch_push_to_celery
        from app.core.config import settings

        was_enabled = settings.CELERY_ENABLED
        try:
            settings.CELERY_ENABLED = False
            assert dispatch_push_to_celery(
                user_id="00000000-0000-0000-0000-000000000001",
                title="t",
                body="b",
                data={},
            ) is False
        finally:
            settings.CELERY_ENABLED = was_enabled