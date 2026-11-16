import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.chat import Chat
from app.models.message import Message
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.tasks.cleanup import (
    _purge_deleted_accounts,
    cleanup_stale_onlines,
    expire_lapsed_premium,
    inactive_user_nudge,
    purge_deleted_accounts,
)


def _user(*, deleted_at=None, is_active=True, last_seen_at=None):
    return User(
        id=uuid4(),
        phone=f"+9891{uuid4().hex[:10]}",
        phone_verified=True,
        is_active=is_active,
        token_version=1,
        registration_status="onboarding_complete",
        deleted_at=deleted_at,
        last_seen_at=last_seen_at,
    )


def _session_factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


class TestExpireLapsedPremium:
    async def test_reports_total_and_lapsed_profiles(self, db_engine, db_session, monkeypatch):
        monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory(db_engine))
        expired_user = _user()
        current_user = _user()
        db_session.add_all(
            [
                expired_user,
                current_user,
                UserProfile(
                    user_id=expired_user.id,
                    premium_until=datetime.now(UTC) - timedelta(days=1),
                ),
                UserProfile(
                    user_id=current_user.id,
                    premium_until=datetime.now(UTC) + timedelta(days=1),
                ),
            ]
        )
        await db_session.commit()

        result = await asyncio.to_thread(expire_lapsed_premium)

        profiles = (await db_session.execute(select(UserProfile))).scalars().all()
        assert result == {"profiles": len(profiles), "lapsed_premium": 1}


class TestCleanupStaleOnlines:
    async def test_deletes_scanned_online_keys(self, monkeypatch):
        from unittest.mock import MagicMock

        redis = MagicMock()
        redis.scan_iter.return_value.__aiter__.return_value = iter([b"online:1", b"online:2"])
        redis.delete = AsyncMock()
        monkeypatch.setattr("app.core.redis.redis_client", redis)

        result = await asyncio.to_thread(cleanup_stale_onlines)

        assert result == "ok"
        assert redis.scan_iter.call_args.args == ("online:*",)
        assert redis.delete.await_args_list[0].args == (b"online:1",)
        assert redis.delete.await_args_list[1].args == (b"online:2",)


class TestPurgeDeletedAccounts:
    async def test_no_candidates_returns_zero(self, db_engine):
        result = await _purge_deleted_accounts(session_factory=_session_factory(db_engine))

        assert result == {"purged": 0}

    async def test_purges_lapsed_user_keeps_recent_and_survives_storage_errors(
        self, db_engine, db_session, monkeypatch
    ):
        from app.services.media_service import MediaService
        from app.services.photo_service import PhotoService

        cutoff = datetime.now(UTC) - timedelta(days=settings.DELETE_ACCOUNT_GRACE_DAYS)
        lapsed = _user(is_active=False, deleted_at=cutoff - timedelta(days=1))
        recent = _user(is_active=False, deleted_at=cutoff + timedelta(days=1))
        peer = _user()
        chat = Chat(id=uuid4(), initiator_id=lapsed.id, recipient_id=peer.id, status="accepted")
        photo = Photo(
            id=uuid4(),
            user_id=lapsed.id,
            url=f"users/{lapsed.id}/photo.jpg",
            order=0,
            is_main=True,
            status="approved",
            face_verified=True,
        )
        message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=lapsed.id,
            receiver_id=peer.id,
            message_type="photo",
            media_url="chat-media/photo.jpg",
        )
        db_session.add_all([lapsed, recent, peer, chat, photo, message])
        await db_session.commit()

        delete_photo = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        delete_media = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        monkeypatch.setattr(PhotoService, "delete_photo", delete_photo)
        monkeypatch.setattr(MediaService, "delete_media", delete_media)

        result = await _purge_deleted_accounts(session_factory=_session_factory(db_engine))

        assert result == {"purged": 1}
        delete_photo.assert_awaited_once_with(str(lapsed.id), str(photo.id))
        delete_media.assert_awaited_once_with(str(chat.id), str(message.id), "photo")
        remaining_lapsed = await db_session.execute(select(User.id).where(User.id == lapsed.id))
        assert remaining_lapsed.scalar_one_or_none() is None
        assert (await db_session.get(User, recent.id)) is not None

    async def test_wrapper_uses_worker_session_factory(self, db_engine, db_session, monkeypatch):
        expired = _user(is_active=False, deleted_at=datetime.now(UTC) - timedelta(days=40))
        db_session.add(expired)
        await db_session.commit()
        monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory(db_engine))
        monkeypatch.setattr("app.services.photo_service.PhotoService.delete_photo", AsyncMock())
        monkeypatch.setattr("app.services.media_service.MediaService.delete_media", AsyncMock())

        result = await asyncio.to_thread(purge_deleted_accounts)

        assert result == {"purged": 1}
        remaining_expired = await db_session.execute(select(User.id).where(User.id == expired.id))
        assert remaining_expired.scalar_one_or_none() is None


class TestInactiveUserNudge:
    async def test_skips_when_fcm_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "FCM_SERVICE_ACCOUNT_PATH", "")

        assert inactive_user_nudge() == {"nudged": 0, "skipped": "no_fcm_configured"}

    async def test_sends_to_active_users_not_seen_in_last_week(
        self, db_engine, db_session, monkeypatch
    ):
        from app.services.push_service import PushService

        monkeypatch.setattr(settings, "FCM_SERVICE_ACCOUNT_PATH", "/configured/for-test.json")
        monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory(db_engine))
        stale = _user(last_seen_at=datetime.now(UTC) - timedelta(days=8))
        recent = _user(last_seen_at=datetime.now(UTC) - timedelta(days=1))
        inactive = _user(is_active=False, last_seen_at=None)
        db_session.add_all([stale, recent, inactive])
        await db_session.commit()
        send_to_user = AsyncMock()
        monkeypatch.setattr(PushService, "send_to_user", send_to_user)

        result = await asyncio.to_thread(inactive_user_nudge)

        assert result == {"nudged": 2}  # stale user and the seeded admin (last_seen_at is NULL)
        assert {call.kwargs["user_id"] for call in send_to_user.await_args_list} == {
            stale.id,
            # The session fixture's admin user has no last_seen_at.
            (await db_session.execute(select(User).where(User.phone == "+989100000000")))
            .scalar_one()
            .id,
        }
        assert all(
            call.kwargs["data"] == {"type": "inactive_nudge"}
            for call in send_to_user.await_args_list
        )
