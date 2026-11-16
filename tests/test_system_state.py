import json
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints import system


class TestSystemStateFiles:
    def test_read_write_and_delete_json_files(self, tmp_path):
        path = tmp_path / "state.json"
        assert system._read_file_json(path) is None

        system._write_file_json(path, {"enabled": True, "message": "maintenance"})
        assert json.loads(path.read_text()) == {
            "enabled": True,
            "message": "maintenance",
        }
        assert not list(tmp_path.glob("*.tmp"))

        path.write_text("not-json")
        assert system._read_file_json(path) is None
        system._delete_file(path)
        system._delete_file(path)
        assert not path.exists()

    def test_write_failure_cleans_temporary_file(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        real_replace = system.os.replace

        def fail_replace(_source, _destination):
            raise OSError("disk full")

        monkeypatch.setattr(system.os, "replace", fail_replace)
        try:
            system._write_file_json(path, {"enabled": True})
        except OSError as exc:
            assert str(exc) == "disk full"
        else:
            raise AssertionError("write error should be propagated")
        finally:
            monkeypatch.setattr(system.os, "replace", real_replace)

        assert not path.exists()
        assert not list(tmp_path.glob("*.tmp"))


class TestSystemStateRedisAndFallback:
    async def test_redis_read_write_delete_helpers_are_best_effort(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"value")
        redis.set = AsyncMock()
        redis.delete = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        assert await system._redis_get("key") == "b'value'"
        await system._redis_set("key", "value")
        await system._redis_delete("key")
        redis.set.assert_awaited_once_with("key", "value")
        redis.delete.assert_awaited_once_with("key")

        redis.get.side_effect = RuntimeError("redis down")
        redis.set.side_effect = RuntimeError("redis down")
        redis.delete.side_effect = RuntimeError("redis down")
        assert await system._redis_get("key") is None
        await system._redis_set("key", "value")
        await system._redis_delete("key")

    async def test_maintenance_status_prefers_redis_and_bootstraps_from_file(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "maintenance.json"
        monkeypatch.setattr(system, "MAINTENANCE_FILE", path)
        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "maintenance_mode": True,
                    "message": "redis value",
                    "start_time": "start",
                    "end_time": None,
                }
            )
        )
        redis.set = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        status = await system.get_maintenance_status()
        assert status.enabled is True
        assert status.message == "redis value"

        path.write_text(
            json.dumps(
                {
                    "maintenance_mode": False,
                    "message": "from file",
                    "start_time": None,
                    "end_time": None,
                }
            )
        )
        redis.get.return_value = None
        status = await system.get_maintenance_status()
        assert status.enabled is False
        assert status.message == "from file"
        redis.set.assert_awaited_with(system._MAINTENANCE_REDIS_KEY, path.read_text())

    async def test_maintenance_set_enable_and_disable_persist_expected_state(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "maintenance.json"
        monkeypatch.setattr(system, "MAINTENANCE_FILE", path)
        redis = MagicMock()
        redis.set = AsyncMock()
        redis.delete = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        enabled = await system.set_maintenance_status(True, "planned")
        assert enabled.enabled is True
        assert enabled.message == "planned"
        assert path.exists()
        assert json.loads(path.read_text())["maintenance_mode"] is True
        assert json.loads(redis.set.await_args.args[1])["maintenance_mode"] is True

        disabled = await system.set_maintenance_status(False)
        assert disabled.enabled is False
        assert disabled.message is None
        assert not path.exists()

    async def test_version_override_uses_file_fallback_and_persists_updates(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "version.json"
        monkeypatch.setattr(system, "VERSION_OVERRIDE_FILE", path)
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        data = {"minimum_versions": {"ios": "3.0.0"}}
        path.write_text(json.dumps(data))
        assert await system.get_version_override() == data
        redis.set.assert_awaited_once_with(system._VERSION_OVERRIDE_REDIS_KEY, json.dumps(data))

        await system.set_version_override({"force_update": True})
        assert json.loads(path.read_text()) == {"force_update": True}
        assert json.loads(redis.set.await_args.args[1]) == {"force_update": True}

        redis.get.return_value = "invalid-json"
        assert await system.get_version_override() == {"force_update": True}

    async def test_version_override_empty_and_maintenance_malformed_redis_fallback(
        self, tmp_path, monkeypatch
    ):
        maintenance_path = tmp_path / "maintenance.json"
        version_path = tmp_path / "version.json"
        monkeypatch.setattr(system, "MAINTENANCE_FILE", maintenance_path)
        monkeypatch.setattr(system, "VERSION_OVERRIDE_FILE", version_path)
        redis = MagicMock()
        redis.get = AsyncMock(return_value="invalid-json")
        redis.set = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        maintenance_path.write_text(json.dumps({"maintenance_mode": True}))
        maintenance = await system.get_maintenance_status()
        assert maintenance.enabled is True

        assert await system.get_version_override() == {}

        redis.get.side_effect = RuntimeError("redis down")
        assert await system.get_version_override() == {}

    async def test_maintenance_status_uses_redis_value_without_file_read(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "missing-maintenance.json"
        monkeypatch.setattr(system, "MAINTENANCE_FILE", path)
        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "maintenance_mode": True,
                    "message": "Redis maintenance",
                    "start_time": "2026-01-01T00:00:00+00:00",
                    "end_time": None,
                }
            )
        )
        redis.set = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        status = await system.get_maintenance_status()

        assert status.enabled is True
        assert status.message == "Redis maintenance"
        assert not path.exists()
        redis.set.assert_not_awaited()

    async def test_maintenance_status_defaults_when_no_redis_or_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(system, "MAINTENANCE_FILE", tmp_path / "absent.json")
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        monkeypatch.setattr(system, "redis_client", redis)

        status = await system.get_maintenance_status()

        assert status.enabled is False
        assert status.message is None
        assert status.start_time is None
        assert status.end_time is None

    async def test_check_admin_auth_legacy_key_comparison(self, monkeypatch):
        from starlette.requests import Request

        monkeypatch.setattr(system.settings, "ADMIN_SECRET_KEY", "admin-secret")

        def request_with(headers):
            return Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "headers": [
                        (key.lower().encode(), value.encode()) for key, value in headers.items()
                    ],
                    "query_string": b"",
                    "server": ("test", 80),
                    "client": ("127.0.0.1", 1234),
                    "scheme": "http",
                }
            )

        assert await system.check_admin_auth(request_with({"X-Admin-Key": "admin-secret"}))
        assert not await system.check_admin_auth(request_with({"X-Admin-Key": "wrong"}))
        assert not await system.check_admin_auth(request_with({}))
