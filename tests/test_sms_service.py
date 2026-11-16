from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import settings
from app.services import sms_service


class TestNormalizeReceptor:
    @pytest.mark.parametrize(
        ("phone", "expected"),
        [
            ("+989381072001", "09381072001"),
            ("989381072001", "09381072001"),
            ("+1 202 555 0110", "12025550110"),
            ("00989381072001", "00989381072001"),
        ],
    )
    def test_normalizes_phone(self, phone, expected):
        assert sms_service.normalize_receptor(phone) == expected


class TestGetToken:
    async def test_returns_cached_token(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value="cached-token")
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)

        assert await sms_service._get_token() == "cached-token"
        redis.get.assert_awaited_once_with(sms_service.SMS_TOKEN_CACHE_KEY)

    async def test_missing_credentials_returns_none_without_http(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "")

        assert await sms_service._get_token() is None

    async def test_mints_and_caches_token_with_minimum_ttl(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        response = MagicMock()
        response.json.return_value = {"access_token": "token", "expires_in": 30}
        post = AsyncMock(return_value=response)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = post
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service._get_token() == "token"
        redis.set.assert_awaited_once_with(sms_service.SMS_TOKEN_CACHE_KEY, "token", ex=60)
        response.raise_for_status.assert_called_once()

    async def test_http_failure_returns_none(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=httpx.ConnectError("mock connection failure"))
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service._get_token() is None

    async def test_missing_access_token_returns_none(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        response = MagicMock()
        response.json.return_value = {"expires_in": 900}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=response)
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service._get_token() is None

    async def test_cache_write_failure_does_not_discard_token(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(side_effect=RuntimeError("cache unavailable"))
        monkeypatch.setattr(sms_service.redis_module, "redis_client", redis)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        response = MagicMock()
        response.json.return_value = {"access_token": "token", "expires_in": 120}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=response)
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service._get_token() == "token"
        redis.set.assert_awaited_once_with(sms_service.SMS_TOKEN_CACHE_KEY, "token", ex=60)


class TestSendVerificationCode:
    async def test_dev_mode_does_not_request_token(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", False)
        get_token = AsyncMock()
        monkeypatch.setattr(sms_service, "_get_token", get_token)

        assert await sms_service.send_verification_code("+989381072001", "123456") is True
        get_token.assert_not_awaited()

    async def test_enabled_without_credentials_returns_false(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", True)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "")

        assert await sms_service.send_verification_code("+989381072001", "123456") is False

    async def test_no_token_returns_false(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", True)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        get_token = AsyncMock(return_value=None)
        monkeypatch.setattr(sms_service, "_get_token", get_token)

        assert await sms_service.send_verification_code("+989381072001", "123456") is False

    async def test_sends_farsi_sms_with_sender_line(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", True)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        monkeypatch.setattr(settings, "SMS_BASE_URL", "https://sms.example.test/")
        monkeypatch.setattr(settings, "SMS_SENDER_LINE", "test-sender")
        monkeypatch.setattr(sms_service, "_get_token", AsyncMock(return_value="token"))
        response = MagicMock()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=response)
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service.send_verification_code("+989381072001", "654321", "fa") is True
        assert client.post.call_args.args == ("https://sms.example.test/send-sms",)
        assert client.post.call_args.kwargs["data"] == {
            "message": "کد تایید باندی: 654321",
            "receptor": "09381072001",
            "sender": "test-sender",
        }
        assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"

    async def test_unknown_language_uses_farsi_template(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", True)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        monkeypatch.setattr(settings, "SMS_SENDER_LINE", "")
        monkeypatch.setattr(sms_service, "_get_token", AsyncMock(return_value="token"))
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service.send_verification_code("+989381072001", "654321", "xx") is True
        data = client.post.call_args.kwargs["data"]
        assert data["message"] == "کد تایید باندی: 654321"
        assert "sender" not in data

    async def test_http_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(settings, "SMS_ENABLED", True)
        monkeypatch.setattr(settings, "SMS_CLIENT_ID", "test-client")
        monkeypatch.setattr(settings, "SMS_CLIENT_SECRET", "test-secret")
        monkeypatch.setattr(sms_service, "_get_token", AsyncMock(return_value="token"))
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=httpx.ConnectError("mock connection failure"))
        monkeypatch.setattr(sms_service.httpx, "AsyncClient", MagicMock(return_value=client))

        assert await sms_service.send_verification_code("+989381072001", "654321") is False
