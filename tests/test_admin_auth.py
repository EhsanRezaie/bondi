# tests/done/test_admin_auth.py
import pytest
from httpx import AsyncClient
from unittest.mock import patch

ADMIN_LOGIN_URL = "/api/v1/admin/login"
ADMIN_USERS_URL = "/api/v1/admin/users"


class TestAdminLogin:

    async def test_login_not_configured(self, client: AsyncClient):
        """Should return 503 when admin credentials not configured."""
        res = await client.post(ADMIN_LOGIN_URL, json={
            "username": "admin",
            "password": "test123",
        })
        assert res.status_code == 503

    async def test_login_wrong_credentials(self, client: AsyncClient):
        """Should reject wrong credentials."""
        with patch("app.api.v1.endpoints.admin_auth.settings") as mock_settings:
            mock_settings.ADMIN_USERNAME = "admin"
            mock_settings.ADMIN_PASSWORD_HASH = "$2b$12$BmmIBosrql7pMrMRmgS9ielrTKurq90SXrUIRwPgKbA.36rRDh0ie"
            res = await client.post(ADMIN_LOGIN_URL, json={
                "username": "admin",
                "password": "wrongpassword",
            })
            assert res.status_code == 401

    async def test_login_success(self, client: AsyncClient):
        """Should return JWT token on valid credentials."""
        from app.core.security import hash_password
        pw_hash = hash_password("testpass123")
        with patch("app.api.v1.endpoints.admin_auth.settings") as mock_settings:
            mock_settings.ADMIN_USERNAME = "testadmin"
            mock_settings.ADMIN_PASSWORD_HASH = pw_hash
            res = await client.post(ADMIN_LOGIN_URL, json={
                "username": "testadmin",
                "password": "testpass123",
            })
            assert res.status_code == 200
            data = res.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            assert data["expires_in"] == 3600


class TestAdminJWTAccess:

    async def test_access_with_valid_jwt(self, client: AsyncClient):
        """Should allow admin access with valid JWT token (no user row needed)."""
        from app.core.security import create_admin_token
        token = create_admin_token("admin")
        res = await client.get(ADMIN_USERS_URL, headers={
            "Authorization": f"Bearer {token}",
        })
        assert res.status_code == 200

    async def test_access_with_invalid_jwt(self, client: AsyncClient):
        """Should reject invalid JWT token."""
        res = await client.get(ADMIN_USERS_URL, headers={
            "Authorization": "Bearer invalid.token.here",
        })
        assert res.status_code == 403

    async def test_access_with_legacy_key(self, client: AsyncClient):
        """Should allow admin access with legacy X-Admin-Key (no user row needed)."""
        from app.core.config import settings
        res = await client.get(ADMIN_USERS_URL, headers={
            "X-Admin-Key": settings.ADMIN_SECRET_KEY,
        })
        assert res.status_code == 200

    async def test_access_without_auth(self, client: AsyncClient):
        """Should reject request without any auth."""
        res = await client.get(ADMIN_USERS_URL)
        assert res.status_code == 403
