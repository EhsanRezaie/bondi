from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import deps, security
from app.core.security import create_access_token, create_admin_token
from app.models.user import User


class TestCurrentUserDependencies:
    async def test_get_current_user_uses_cached_snapshot_without_db(self, monkeypatch):
        user_id = uuid4()
        cached_user = SimpleNamespace(id=user_id, is_active=True, token_version=3)
        monkeypatch.setattr(deps, "get_cached_auth_user", AsyncMock(return_value=cached_user))
        monkeypatch.setattr(deps, "cache_auth_user", AsyncMock())
        session = MagicMock()
        session.execute = AsyncMock()

        result = await deps.get_current_user(
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=create_access_token(str(user_id), 3)
            ),
            session,
        )

        assert result is cached_user
        session.execute.assert_not_awaited()

    async def test_get_current_user_falls_back_to_db_and_caches(self, monkeypatch):
        user = User(id=uuid4(), phone="+989111111111", is_active=True, token_version=2)
        monkeypatch.setattr(deps, "get_cached_auth_user", AsyncMock(return_value=None))
        cache_snapshot = AsyncMock()
        monkeypatch.setattr(deps, "cache_auth_user", cache_snapshot)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        resolved = await deps.get_current_user(
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=create_access_token(str(user.id), 2)
            ),
            session,
        )

        assert resolved is user
        cache_snapshot.assert_awaited_once()
        assert cache_snapshot.await_args.args[1:] == (user, 2)

    @pytest.mark.parametrize(
        ("credentials", "cached", "expected_status", "message"),
        [
            (None, None, 401, "Missing authentication token"),
            (
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-token"),
                None,
                401,
                "Invalid or expired token",
            ),
            (
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=create_access_token(str(uuid4()), 1)
                ),
                SimpleNamespace(is_active=False, token_version=1),
                403,
                "Account is deactivated",
            ),
            (
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=create_access_token(str(uuid4()), 1)
                ),
                SimpleNamespace(is_active=True, token_version=2),
                401,
                "Token revoked. Please login again.",
            ),
        ],
    )
    async def test_get_current_user_rejects_invalid_cached_auth(
        self, credentials, cached, expected_status, message, monkeypatch
    ):
        monkeypatch.setattr(deps, "get_cached_auth_user", AsyncMock(return_value=cached))
        with pytest.raises(HTTPException) as exc:
            await deps.get_current_user(credentials, MagicMock())
        assert exc.value.status_code == expected_status
        assert exc.value.detail == message

    async def test_get_current_user_id_uses_cache_and_validates_payload(self, monkeypatch):
        user_id = uuid4()
        credential = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token(str(user_id), 4)
        )
        monkeypatch.setattr(
            deps,
            "get_cached_auth_user",
            AsyncMock(return_value=SimpleNamespace(is_active=True, token_version=4)),
        )
        session = MagicMock()
        session.execute = AsyncMock()

        assert await deps.get_current_user_id(credential, session) == user_id
        session.execute.assert_not_awaited()

        bad_subject = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token("not-a-uuid", 4)
        )
        with pytest.raises(HTTPException) as exc:
            await deps.get_current_user_id(bad_subject, session)
        assert exc.value.detail == "Invalid token payload"

    async def test_get_current_user_id_db_fallback_statuses(self, monkeypatch):
        user_id = uuid4()
        credential = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token(str(user_id), 5)
        )
        monkeypatch.setattr(deps, "get_cached_auth_user", AsyncMock(return_value=None))
        session = MagicMock()
        result = MagicMock()
        session.execute = AsyncMock(return_value=result)

        result.first.return_value = None
        with pytest.raises(HTTPException) as missing:
            await deps.get_current_user_id(credential, session)
        assert missing.value.status_code == 401
        assert missing.value.detail == "User not found"

        result.first.return_value = (False, 5)
        with pytest.raises(HTTPException) as inactive:
            await deps.get_current_user_id(credential, session)
        assert inactive.value.status_code == 403

        result.first.return_value = (True, 6)
        with pytest.raises(HTTPException) as revoked:
            await deps.get_current_user_id(credential, session)
        assert revoked.value.status_code == 401

        result.first.return_value = (True, 5)
        assert await deps.get_current_user_id(credential, session) == user_id

    async def test_get_current_user_id_rejects_cached_inactive_and_revoked_users(self, monkeypatch):
        user_id = uuid4()
        credential = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token(str(user_id), 7)
        )
        session = MagicMock()
        session.execute = AsyncMock()

        for snapshot, expected_status, expected_detail in (
            (SimpleNamespace(is_active=False, token_version=7), 403, "Account is deactivated"),
            (
                SimpleNamespace(is_active=True, token_version=8),
                401,
                "Token revoked. Please login again.",
            ),
        ):
            monkeypatch.setattr(deps, "get_cached_auth_user", AsyncMock(return_value=snapshot))
            with pytest.raises(HTTPException) as exc:
                await deps.get_current_user_id(credential, session)
            assert exc.value.status_code == expected_status
            assert exc.value.detail == expected_detail

    async def test_get_current_user_db_rejects_missing_inactive_and_revoked_rows(self):
        user_id = uuid4()
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=create_access_token(str(user_id), 9)
        )
        session = MagicMock()
        result = MagicMock()
        session.execute = AsyncMock(return_value=result)

        result.scalar_one_or_none.return_value = None
        with pytest.raises(HTTPException) as missing:
            await deps.get_current_user_db(credentials, session)
        assert missing.value.status_code == 401
        assert missing.value.detail == "User not found"

        result.scalar_one_or_none.return_value = SimpleNamespace(is_active=False)
        with pytest.raises(HTTPException) as inactive:
            await deps.get_current_user_db(credentials, session)
        assert inactive.value.status_code == 403

        result.scalar_one_or_none.return_value = SimpleNamespace(is_active=True, token_version=10)
        with pytest.raises(HTTPException) as revoked:
            await deps.get_current_user_db(credentials, session)
        assert revoked.value.status_code == 401

    async def test_current_user_premium_and_active_guards(self):
        user = SimpleNamespace(is_active=True, profile=SimpleNamespace(is_premium=True))
        assert await deps.get_current_active_user(user) is user
        assert await deps.get_current_premium_user(user) is user

        with pytest.raises(HTTPException) as inactive:
            await deps.get_current_active_user(SimpleNamespace(is_active=False))
        assert inactive.value.status_code == 403

        for profile in (None, SimpleNamespace(is_premium=False)):
            with pytest.raises(HTTPException) as non_premium:
                await deps.get_current_premium_user(SimpleNamespace(profile=profile))
            assert non_premium.value.status_code == 403


class TestAdminDependency:
    async def test_get_admin_user_accepts_jwt_and_legacy_key(self, monkeypatch):
        monkeypatch.setattr(security, "verify_admin_token", lambda token: {"sub": "operator"})
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {create_admin_token('operator')}"}
        identity = await deps.get_admin_user(request)
        assert identity.id == "operator"

        monkeypatch.setattr(security, "verify_admin_token", lambda _token: None)
        request.headers = {"X-Admin-Key": deps.settings.ADMIN_SECRET_KEY}
        identity = await deps.get_admin_user(request)
        assert identity.id == deps.settings.ADMIN_USERNAME

    async def test_get_admin_user_rejects_bad_credentials(self, monkeypatch):
        monkeypatch.setattr(security, "verify_admin_token", lambda _token: None)
        request = MagicMock()
        request.headers = {"Authorization": "Bearer invalid", "X-Admin-Key": "wrong"}

        with pytest.raises(HTTPException) as exc:
            await deps.get_admin_user(request)
        assert exc.value.status_code == 403
