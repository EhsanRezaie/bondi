import pytest
from httpx import AsyncClient

from datetime import date

from app.core.security import decode_token


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

REQUEST_CODE_URL = "/api/v1/auth/request-code"
VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
HEALTH_URL = "/api/v1/auth/health"

VALID_PHONE = "+989379191281"
NEW_PHONE = "+989112345678"
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD = {
    "name": "Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "sexual_orientation": "straight",
    "bio": "This is my bio",
    "height": 180,
    "weight": 75,
    "body_type": "athletic",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "open_to_children",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Software Engineer",
    "religion": "Islam",
    "ethnicity": "Persian",
    "political_orientation": "moderate",
    "lat": 35.6892,
    "lng": 51.3890,
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
    "interests": ["Music", "Sport", "Travel"],
}

def calculate_age(birth_date: str) -> int:
    today = date.today()
    birth = date.fromisoformat(birth_date)
    age = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        age -= 1
    return age

async def verify_new_user(client: AsyncClient, mock_verification_code, phone: str = NEW_PHONE) -> dict:
    """Helper: complete phone verification as a NEW user (returns verify response)."""
    await mock_verification_code(phone, VALID_CODE)
    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["is_new_user"] is True
    return data

async def register_user_full(client: AsyncClient, mock_verification_code, phone: str = NEW_PHONE) -> dict:
    """Helper: register a new user through the full flow and complete the profile."""
    data = await verify_new_user(client, mock_verification_code, phone)

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=COMPLETE_PROFILE_PAYLOAD,
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# POST /auth/request-code
# ---------------------------------------------------------------------------

class TestRequestCode:

    async def test_request_code_success(self, client: AsyncClient):
        """Should send an SMS OTP for a valid phone."""
        res = await client.post(REQUEST_CODE_URL, json={"phone": VALID_PHONE})
        assert res.status_code == 200
        data = res.json()
        assert data["phone"] == VALID_PHONE
        assert data["expires_in"] == 300
        assert data["resend_in"] == 60
        assert "message" in data

    async def test_request_code_cooldown(self, client: AsyncClient):
        """A second request before the cooldown expires must be rejected."""
        res = await client.post(REQUEST_CODE_URL, json={"phone": VALID_PHONE})
        assert res.status_code == 200

        res2 = await client.post(REQUEST_CODE_URL, json={"phone": VALID_PHONE})
        assert res2.status_code == 429
        assert "wait" in res2.json()["detail"].lower()

    async def test_request_code_invalid_phone(self, client: AsyncClient):
        """Should reject a phone without a country code."""
        res = await client.post(REQUEST_CODE_URL, json={"phone": "9123456789"})
        assert res.status_code == 422

    async def test_request_code_short_phone(self, client: AsyncClient):
        """Should reject a phone with too few digits."""
        res = await client.post(REQUEST_CODE_URL, json={"phone": "+12"})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/verify-code
# ---------------------------------------------------------------------------

class TestVerifyCode:

    async def test_verify_code_new_user(self, client: AsyncClient, mock_verification_code):
        """Should create a new user on first verification."""
        await mock_verification_code(NEW_PHONE, VALID_CODE)

        res = await client.post(VERIFY_CODE_URL, json={"phone": NEW_PHONE, "code": VALID_CODE})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["is_new_user"] is True
        assert data["user"]["phone"] == NEW_PHONE
        assert data["user"]["is_profile_complete"] is False

    async def test_verify_code_existing_user(self, client: AsyncClient, mock_verification_code):
        """Verifying an existing phone logs in and does NOT create a new user."""
        await register_user_full(client, mock_verification_code, NEW_PHONE)

        await mock_verification_code(NEW_PHONE, VALID_CODE)
        res = await client.post(VERIFY_CODE_URL, json={"phone": NEW_PHONE, "code": VALID_CODE})
        assert res.status_code == 200
        data = res.json()
        assert data["is_new_user"] is False
        assert data["user"]["phone"] == NEW_PHONE

    async def test_verify_code_invalid_code(self, client: AsyncClient, mock_verification_code):
        """Should reject an invalid code and count the attempt."""
        await mock_verification_code(NEW_PHONE, VALID_CODE)

        res = await client.post(VERIFY_CODE_URL, json={"phone": NEW_PHONE, "code": "000000"})
        assert res.status_code == 400
        assert "Invalid code" in res.json()["detail"]
        assert "attempt" in res.json()["detail"]

    async def test_verify_code_no_code(self, client: AsyncClient):
        """Should reject when no code was requested/stored."""
        res = await client.post(VERIFY_CODE_URL, json={"phone": VALID_PHONE, "code": VALID_CODE})
        assert res.status_code == 400
        assert "expired" in res.json()["detail"]

    async def test_verify_code_invalid_phone(self, client: AsyncClient):
        """Should reject a phone without a country code."""
        res = await client.post(VERIFY_CODE_URL, json={"phone": "9123456789", "code": VALID_CODE})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/register/complete
# ---------------------------------------------------------------------------

class TestRegisterComplete:

    async def test_register_complete_success(self, client: AsyncClient, mock_verification_code):
        """Should complete profile with all fields."""
        data = await verify_new_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE_PAYLOAD, headers=headers)
        assert res.status_code == 200
        user_data = res.json()["user"]
        assert user_data["name"] == "Test User"
        assert user_data["age"] == calculate_age("1995-06-15")
        assert user_data["gender"] == "male"
        assert user_data["height"] == 180
        assert user_data["weight"] == 75
        assert user_data["body_type"] == "athletic"
        assert user_data["relationship_status"] == "single"
        assert user_data["is_profile_complete"] is True

    async def test_register_complete_requires_auth(self, client: AsyncClient):
        """Should require authentication."""
        res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE_PAYLOAD)
        assert res.status_code == 401

    async def test_register_complete_already_complete(self, client: AsyncClient, mock_verification_code):
        """Should reject if profile already complete."""
        data = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE_PAYLOAD, headers=headers)
        assert res.status_code == 400
        assert "already complete" in res.json()["detail"]

    async def test_register_complete_invalid_gender(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid gender."""
        data = await verify_new_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        payload = {**COMPLETE_PROFILE_PAYLOAD, "gender": "invalid"}
        res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefresh:

    async def test_refresh_success(self, client: AsyncClient, mock_verification_code):
        """Should refresh tokens successfully."""
        data = await register_user_full(client, mock_verification_code)

        res = await client.post(REFRESH_URL, json={"refresh_token": data["refresh_token"]})
        assert res.status_code == 200
        new_data = res.json()
        assert "access_token" in new_data
        assert "refresh_token" in new_data

    async def test_refresh_token_rotation(self, client: AsyncClient, mock_verification_code):
        """Old refresh token must be invalid after rotation."""
        data = await register_user_full(client, mock_verification_code)
        old_refresh = data["refresh_token"]

        res = await client.post(REFRESH_URL, json={"refresh_token": old_refresh})
        assert res.status_code == 200

        res2 = await client.post(REFRESH_URL, json={"refresh_token": old_refresh})
        assert res2.status_code == 401
        assert "revoked" in res2.json()["detail"]

    async def test_refresh_reuse_revokes_family(self, client: AsyncClient, mock_verification_code):
        """Replaying a rotated refresh token must revoke the WHOLE family."""
        data = await register_user_full(client, mock_verification_code)
        refresh_a = data["refresh_token"]

        res_b = await client.post(REFRESH_URL, json={"refresh_token": refresh_a})
        assert res_b.status_code == 200
        refresh_b = res_b.json()["refresh_token"]

        res_c = await client.post(REFRESH_URL, json={"refresh_token": refresh_b})
        assert res_c.status_code == 200
        refresh_c = res_c.json()["refresh_token"]

        res_evil = await client.post(REFRESH_URL, json={"refresh_token": refresh_b})
        assert res_evil.status_code == 401

        res_c2 = await client.post(REFRESH_URL, json={"refresh_token": refresh_c})
        assert res_c2.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:

    async def test_logout_success(self, client: AsyncClient, mock_verification_code):
        """Should logout successfully."""
        data = await register_user_full(client, mock_verification_code)

        res = await client.post(LOGOUT_URL, json={"refresh_token": data["refresh_token"]})
        assert res.status_code == 204

    async def test_logout_revokes_token(self, client: AsyncClient, mock_verification_code):
        """After logout, refresh token should not work."""
        data = await register_user_full(client, mock_verification_code)
        refresh_token = data["refresh_token"]

        await client.post(LOGOUT_URL, json={"refresh_token": refresh_token})

        res = await client.post(REFRESH_URL, json={"refresh_token": refresh_token})
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# GET /auth/health
# ---------------------------------------------------------------------------

class TestHealthCheck:

    async def test_health_check_returns_redis_status(self, client: AsyncClient):
        """Health endpoint should show Redis status."""
        res = await client.get(HEALTH_URL)
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "redis" in data


# ---------------------------------------------------------------------------
# Token Versioning Tests
# ---------------------------------------------------------------------------

class TestTokenVersioning:

    async def test_token_contains_version(self, client: AsyncClient, mock_verification_code):
        """Access token should contain version number."""
        data = await register_user_full(client, mock_verification_code)

        payload = decode_token(data["access_token"], "access")
        assert payload is not None
        assert "ver" in payload
        assert payload["ver"] == 1
