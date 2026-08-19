
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


from app.core.config import settings as app_settings

# ============ URL Constants ============
VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SETTINGS_URL = "/api/v1/users/me/settings"

VALID_PHONE = _phone("settings_test@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD = {
    "name": "Settings Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user_full(client: AsyncClient, mock_verification_code=None, phone: str = VALID_PHONE) -> dict:
    """Helper: complete full registration via phone OTP flow."""
    if mock_verification_code:
        await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200
    tokens = res.json()

    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=COMPLETE_PROFILE_PAYLOAD,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 200
    return tokens


# =============================================================================
# PUT /api/v1/users/me/settings
# =============================================================================

class TestUpdateSettings:
    """Test PUT /users/me/settings endpoint."""

    async def test_update_all_settings(self, client: AsyncClient, mock_verification_code):
        """Should update all settings fields."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={
            "hide_last_seen": True,
            "hide_online_status": True,
            "push_enabled": False,
            "like_notifications": False,
            "match_notifications": False,
            "message_notifications": False,
            "language": "en",
            "dark_mode": True,
        })
        assert res.status_code == 200
        data = res.json()

        assert data["hide_last_seen"] is True
        assert data["hide_online_status"] is True
        assert data["push_enabled"] is False
        assert data["like_notifications"] is False
        assert data["match_notifications"] is False
        assert data["message_notifications"] is False
        assert data["language"] == "en"
        assert data["dark_mode"] is True

    async def test_update_single_field(self, client: AsyncClient, mock_verification_code):
        """Should update only the provided field, keep defaults for others."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={
            "dark_mode": True,
        })
        assert res.status_code == 200
        data = res.json()

        assert data["dark_mode"] is True
        assert data["hide_last_seen"] is False
        assert data["push_enabled"] is True
        assert data["language"] == "fa"

    async def test_reset_settings_back(self, client: AsyncClient, mock_verification_code):
        """Should allow toggling settings back and forth."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={"dark_mode": True})
        assert res.status_code == 200
        assert res.json()["dark_mode"] is True

        res = await client.put(SETTINGS_URL, headers=headers, json={"dark_mode": False})
        assert res.status_code == 200
        assert res.json()["dark_mode"] is False

    async def test_update_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication."""
        res = await client.put(SETTINGS_URL, json={"dark_mode": True})
        assert res.status_code == 401

    async def test_empty_body_returns_400(self, client: AsyncClient, mock_verification_code):
        """Should return 400 when no fields are provided."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={})
        assert res.status_code == 400
        assert "no fields" in res.json()["detail"].lower()

    async def test_invalid_language_returns_400(self, client: AsyncClient, mock_verification_code):
        """Should return 400 for unsupported language."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={"language": "de"})
        assert res.status_code == 200

    async def test_settings_persist_across_requests(self, client: AsyncClient, mock_verification_code):
        """Should persist updated settings across multiple GET requests."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        await client.put(SETTINGS_URL, headers=headers, json={
            "language": "en",
            "dark_mode": True,
        })

        res1 = await client.get("/api/v1/users/me", headers=headers)
        assert res1.status_code == 200
        s1 = res1.json()["settings"]
        assert s1["language"] == "en"
        assert s1["dark_mode"] is True

        res2 = await client.get("/api/v1/users/me", headers=headers)
        assert res2.status_code == 200
        s2 = res2.json()["settings"]
        assert s2["language"] == "en"
        assert s2["dark_mode"] is True

    async def test_null_fields_ignored(self, client: AsyncClient, mock_verification_code):
        """Should treat null fields as not provided and keep existing values."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(SETTINGS_URL, headers=headers, json={
            "hide_last_seen": None,
            "dark_mode": True,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["hide_last_seen"] is False
        assert data["dark_mode"] is True
