"""Phase 2.3 — settings validation: fail fast on weak secrets in production.

Dev/test environments must still boot with short convenience keys; only
ENVIRONMENT=production enforces strength.
"""

import pytest


def _make_settings(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    secret_key: str,
    encryption: str = "x" * 40,
    admin_key: str = "k" * 40,
):
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("S3_SECRET_KEY", "x")
    monkeypatch.setenv("ADMIN_SECRET_KEY", admin_key)
    monkeypatch.setenv("SECRET_KEY", secret_key)
    monkeypatch.setenv("ENCRYPTION_SECRET", encryption)
    return Settings()  # type: ignore[call-arg]


def test_production_rejects_short_secret_key(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(Exception, match="too weak"):
        _make_settings(monkeypatch, "production", "qMiVidSmH6dl508w")


def test_production_rejects_weak_marker_secret(monkeypatch: pytest.MonkeyPatch):
    weak = "changeme" + "a" * 40  # long enough, but contains a weak marker
    with pytest.raises(Exception, match="too weak"):
        _make_settings(monkeypatch, "production", weak)


def test_production_rejects_short_encryption_secret(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(Exception, match="ENCRYPTION_SECRET"):
        _make_settings(monkeypatch, "production", "a" * 40, encryption="short-key")


def test_production_accepts_strong_secrets(monkeypatch: pytest.MonkeyPatch):
    s = _make_settings(monkeypatch, "production", "9rz5fZ6pcH7nUEouB7QWbDyFjVEABM2t7naqR_p7ars")
    assert s.ENVIRONMENT == "production"


def test_development_allows_short_secret_key(monkeypatch: pytest.MonkeyPatch):
    s = _make_settings(monkeypatch, "development", "qMiVidSmH6dl508w")
    assert s.ENVIRONMENT == "development"


def test_test_environment_allows_short_secret_key(monkeypatch: pytest.MonkeyPatch):
    s = _make_settings(monkeypatch, "test", "qMiVidSmH6dl508w")
    assert s.ENVIRONMENT == "test"
def test_production_rejects_short_admin_secret(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(Exception, match="ADMIN_SECRET_KEY"):
        _make_settings(
            monkeypatch,
            "production",
            "9rz5fZ6pcH7nUEouB7QWbDyFjVEABM2t7naqR_p7ars",
            admin_key="short",
        )
def test_development_allows_short_admin_secret(monkeypatch: pytest.MonkeyPatch):
    s = _make_settings(
        monkeypatch, "development", "qMiVidSmH6dl508w", admin_key="short-admin-key"
    )
    assert s.ENVIRONMENT == "development"
