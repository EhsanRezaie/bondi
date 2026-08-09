import pytest
from httpx import AsyncClient

from datetime import date

from app.core.config import settings
from app.core.security import decode_token


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password"
HEALTH_URL = "/api/v1/auth/health"

VALID_EMAIL = "test@example.com"
VALID_PASSWORD = "strongpass123"
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
    "children_status": "dont_have",
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

async def register_user_full(client: AsyncClient, mock_verification_code=None) -> dict:
    """Helper: complete full registration flow."""
    # Step 1: Init
    res = await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
    assert res.status_code == 200, res.text
    
    # Step 2: Store verification code in Redis if mock provided
    if mock_verification_code:
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
    
    # Step 3: Verify
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": VALID_EMAIL,
        "code": VALID_CODE,
        "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    
    # Step 4: Complete profile
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=COMPLETE_PROFILE_PAYLOAD,
        headers=headers,
    )
    assert res.status_code == 200, res.text
    
    return res.json()


# ---------------------------------------------------------------------------
# POST /auth/register/init
# ---------------------------------------------------------------------------

class TestRegisterInit:
    
    async def test_register_init_success(self, client: AsyncClient):
        """Should send verification code to email."""
        res = await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        assert res.status_code == 200
        data = res.json()
        assert data["email"] == VALID_EMAIL
        assert data["expires_in"] == 300
        assert "message" in data
    
    async def test_register_init_duplicate_email(self, client: AsyncClient, mock_verification_code):
        """Should return same response for existing email (enumeration protection)."""
        await register_user_full(client, mock_verification_code)

        res = await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        assert res.status_code == 200
        assert "verification" in res.json()["message"].lower()
    
    async def test_register_init_invalid_email(self, client: AsyncClient):
        """Should reject invalid email format."""
        res = await client.post(REGISTER_INIT_URL, json={"email": "not-an-email"})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/register/verify
# ---------------------------------------------------------------------------

class TestRegisterVerify:
    
    async def test_register_verify_success(self, client: AsyncClient, mock_verification_code):
        """Should verify code and create user."""
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
        
        res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": VALID_CODE,
            "password": VALID_PASSWORD,
        })
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user_id"] is not None
    
    async def test_register_verify_invalid_code(self, client: AsyncClient):
        """Should reject invalid verification code with attempt count."""
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})

        res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": "000000",
            "password": VALID_PASSWORD,
        })
        assert res.status_code == 400
        assert "Invalid code" in res.json()["detail"]
        assert "attempt" in res.json()["detail"]
    
    async def test_register_verify_password_too_short(self, client: AsyncClient, mock_verification_code):
        """Should reject password shorter than 8 characters."""
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
        
        res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": VALID_CODE,
            "password": "short",
        })
        assert res.status_code == 422
    
    async def test_register_verify_duplicate_email(self, client: AsyncClient, mock_verification_code):
        """Should reject if email already exists."""
        await register_user_full(client, mock_verification_code)
        
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
        
        res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": VALID_CODE,
            "password": VALID_PASSWORD,
        })
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# POST /auth/register/complete
# ---------------------------------------------------------------------------

class TestRegisterComplete:
    
    async def test_register_complete_success(self, client: AsyncClient, mock_verification_code):
        """Should complete profile with all fields."""
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
        
        verify_res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": VALID_CODE,
            "password": VALID_PASSWORD,
        })
        data = verify_res.json()
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        res = await client.post(
            REGISTER_COMPLETE_URL,
            json=COMPLETE_PROFILE_PAYLOAD,
            headers=headers,
        )
        assert res.status_code == 200
        user_data = res.json()
        assert user_data["user"]["name"] == "Test User"
        assert user_data["user"]["age"] == calculate_age("1995-06-15")
        assert user_data["user"]["gender"] == "male"
        assert user_data["user"]["height"] == 180
        assert user_data["user"]["weight"] == 75
        assert user_data["user"]["body_type"] == "athletic"
        assert user_data["user"]["relationship_status"] == "single"
    
    async def test_register_complete_requires_auth(self, client: AsyncClient):
        """Should require authentication."""
        res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE_PAYLOAD)
        assert res.status_code == 401
    
    async def test_register_complete_already_complete(self, client: AsyncClient, mock_verification_code):
        """Should reject if profile already complete."""
        token_data = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {token_data['access_token']}"}
        
        res = await client.post(
            REGISTER_COMPLETE_URL,
            json=COMPLETE_PROFILE_PAYLOAD,
            headers=headers,
        )
        assert res.status_code == 400
        assert "already complete" in res.json()["detail"]
    
    async def test_register_complete_invalid_gender(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid gender."""
        await client.post(REGISTER_INIT_URL, json={"email": VALID_EMAIL})
        await mock_verification_code(VALID_EMAIL, VALID_CODE)
        
        verify_res = await client.post(REGISTER_VERIFY_URL, json={
            "email": VALID_EMAIL,
            "code": VALID_CODE,
            "password": VALID_PASSWORD,
        })
        data = verify_res.json()
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        payload = {**COMPLETE_PROFILE_PAYLOAD, "gender": "invalid"}
        res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    
    async def test_login_success(self, client: AsyncClient, mock_verification_code):
        """Should login successfully."""
        await register_user_full(client, mock_verification_code)
        
        res = await client.post(LOGIN_URL, json={
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        })
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
    
    async def test_login_wrong_password(self, client: AsyncClient, mock_verification_code):
        """Should reject wrong password."""
        await register_user_full(client, mock_verification_code)
        
        res = await client.post(LOGIN_URL, json={
            "email": VALID_EMAIL,
            "password": "wrongpassword",
        })
        assert res.status_code == 401
        assert "Incorrect" in res.json()["detail"]
    
    async def test_login_nonexistent_email(self, client: AsyncClient):
        """Should reject nonexistent email."""
        res = await client.post(LOGIN_URL, json={
            "email": "nobody@example.com",
            "password": "somepassword",
        })
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefresh:
    
    async def test_refresh_success(self, client: AsyncClient, mock_verification_code):
        """Should refresh tokens successfully."""
        data = await register_user_full(client, mock_verification_code)
        
        res = await client.post(REFRESH_URL, json={
            "refresh_token": data["refresh_token"]
        })
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
        """Replaying a rotated refresh token must revoke the WHOLE family.

        A token acquired in the same family must stop working afterwards,
        forcing a re-login everywhere (refresh-theft detection).
        """
        data = await register_user_full(client, mock_verification_code)
        refresh_a = data["refresh_token"]

        # First rotation: A -> B, same family.
        res_b = await client.post(REFRESH_URL, json={"refresh_token": refresh_a})
        assert res_b.status_code == 200
        refresh_b = res_b.json()["refresh_token"]

        # Second rotation: B -> C, still same family.
        res_c = await client.post(REFRESH_URL, json={"refresh_token": refresh_b})
        assert res_c.status_code == 200
        refresh_c = res_c.json()["refresh_token"]

        # Attacker replays the already-rotated B → family is revoked.
        res_evil = await client.post(REFRESH_URL, json={"refresh_token": refresh_b})
        assert res_evil.status_code == 401

        # Even the fresh C token — same family — must now be dead.
        res_c2 = await client.post(REFRESH_URL, json={"refresh_token": refresh_c})
        assert res_c2.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    
    async def test_logout_success(self, client: AsyncClient, mock_verification_code):
        """Should logout successfully."""
        data = await register_user_full(client, mock_verification_code)
        
        res = await client.post(LOGOUT_URL, json={
            "refresh_token": data["refresh_token"]
        })
        assert res.status_code == 204
    
    async def test_logout_revokes_token(self, client: AsyncClient, mock_verification_code):
        """After logout, refresh token should not work."""
        data = await register_user_full(client, mock_verification_code)
        refresh_token = data["refresh_token"]
        
        await client.post(LOGOUT_URL, json={"refresh_token": refresh_token})
        
        res = await client.post(REFRESH_URL, json={"refresh_token": refresh_token})
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/change-password
# ---------------------------------------------------------------------------

class TestChangePassword:
    
    async def test_change_password_success(self, client: AsyncClient, mock_verification_code):
        """Should change password successfully."""
        data = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        res = await client.post(
            CHANGE_PASSWORD_URL,
            json={"old_password": VALID_PASSWORD, "new_password": "newpass456"},
            headers=headers,
        )
        assert res.status_code == 204
        
        login_res = await client.post(LOGIN_URL, json={
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        })
        assert login_res.status_code == 401
        
        login_res2 = await client.post(LOGIN_URL, json={
            "email": VALID_EMAIL,
            "password": "newpass456",
        })
        assert login_res2.status_code == 200
    
    async def test_change_password_wrong_old_password(self, client: AsyncClient, mock_verification_code):
        """Should reject wrong old password."""
        data = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        res = await client.post(
            CHANGE_PASSWORD_URL,
            json={"old_password": "wrongpassword", "new_password": "newpass456"},
            headers=headers,
        )
        assert res.status_code == 401
        assert "Incorrect old password" in res.json()["detail"]
    
    async def test_change_password_requires_auth(self, client: AsyncClient):
        """Should require authentication."""
        res = await client.post(
            CHANGE_PASSWORD_URL,
            json={"old_password": VALID_PASSWORD, "new_password": "newpass456"},
        )
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
    
    async def test_token_version_increments_after_password_change(self, client: AsyncClient, mock_verification_code):
        """Token version should increment after password change."""
        data = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        
        payload1 = decode_token(data["access_token"], "access")
        assert payload1 is not None
        assert payload1["ver"] == 1
        
        await client.post(
            CHANGE_PASSWORD_URL,
            json={"old_password": VALID_PASSWORD, "new_password": "newpass456"},
            headers=headers,
        )
        
        login_res = await client.post(LOGIN_URL, json={
            "email": VALID_EMAIL,
            "password": "newpass456",
        })
        assert login_res.status_code == 200
        data2 = login_res.json()
        
        payload2 = decode_token(data2["access_token"], "access")
        assert payload2 is not None
        assert payload2["ver"] == 2