import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import delete


def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib

    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


# ============ URL Constants ============
VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
USERS_ME_URL = "/api/v1/users/me"
USERS_ME_DELETE_CODE_URL = "/api/v1/users/me/delete-code"
USERS_ME_LOCATION_URL = "/api/v1/users/me/location"
USERS_ME_LOCATION_TEXT_URL = "/api/v1/users/me/location-text"

# ============ Test Data ============
VALID_PHONE = _phone("test@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD = {
    "name": "Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "sexual_orientation": "straight",
    "bio": "Hello, I'm a test user",
    "height": 180,
    "weight": 75,
    "body_type": "athletic",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "open_to_children",
    "smoking": "never",
    "drinking": "socially",
    "here_for": "long_term_relationship",
    "pets": "cat",
    "workout_frequency": "regularly",
    "zodiac_sign": "leo",
    "education": "bachelor",
    "workplace": "Tech Corp",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
    "lat": 35.6892,
    "lng": 51.3890,
}

MINIMAL_PROFILE_PAYLOAD = {
    "name": "Minimal User",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}


def calculate_age(birth_date: str) -> int:
    today = date.today()
    birth = date.fromisoformat(birth_date)
    age = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        age -= 1
    return age


# ============ EXACTLY LIKE test_auth.py ============
async def register_user_full(
    client: AsyncClient, mock_verification_code=None, phone: str = VALID_PHONE
) -> dict:
    """Helper: complete full registration via phone OTP flow."""
    if mock_verification_code:
        await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=COMPLETE_PROFILE_PAYLOAD,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    return res.json()


# ============ Helper for custom phone ============
async def register_user_full_custom(
    client: AsyncClient,
    mock_verification_code,
    phone: str,
    complete_payload: dict = None,
) -> dict:
    """Helper: complete full registration via phone OTP with a custom phone."""
    complete_payload = complete_payload or COMPLETE_PROFILE_PAYLOAD

    await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=complete_payload,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    return res.json()


def assert_profile_fields(data: dict, expected: dict):
    """Helper to assert profile fields match expected values."""
    profile_fields = [
        "name",
        "gender",
        "sexual_orientation",
        "bio",
        "height",
        "weight",
        "body_type",
        "relationship_status",
        "living_situation",
        "children_status",
        "smoking",
        "drinking",
        "here_for",
        "pets",
        "workout_frequency",
        "zodiac_sign",
        "education",
        "workplace",
        "religion",
        "ethnicity",
        "political_orientation",
        "languages",
        "country",
        "province",
        "city",
        "lat",
        "lng",
        "location_manual",
    ]

    for field in profile_fields:
        if field in expected:
            actual = data.get(field)
            expected_val = expected[field]
            assert actual == expected_val, (
                f"Field {field} mismatch: expected {expected_val}, got {actual}"
            )


async def _delete_me(client: AsyncClient, headers: dict, payload: dict = None):
    """httpx's AsyncClient.delete() doesn't forward `json=` in this version."""
    return await client.request("DELETE", USERS_ME_URL, headers=headers, json=payload or {})


# =============================================================================
# GET /api/v1/users/me
# =============================================================================


class TestGetMe:
    async def test_get_me_success(self, client: AsyncClient, mock_verification_code):
        """Should return current user profile with all fields."""
        result = await register_user_full(client, mock_verification_code)
        token = result["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()

        assert data["id"] is not None
        assert "email" not in data  # phone-only auth; email was removed
        assert data["is_active"] is True
        assert data["is_profile_complete"] is True
        assert data["profile_completion"] == 100
        assert data["created_at"] is not None

        assert_profile_fields(data, COMPLETE_PROFILE_PAYLOAD)

        assert data["age"] == calculate_age("1995-06-15")
        assert "is_premium" in data
        assert "premium_until" in data
        assert data["is_verified"] is False

        assert data["settings"] is not None
        settings = data["settings"]
        assert "hide_last_seen" in settings
        assert "hide_online_status" in settings
        assert "push_enabled" in settings
        assert "like_notifications" in settings
        assert "match_notifications" in settings
        assert "message_notifications" in settings
        assert "language" in settings
        assert "dark_mode" in settings

        assert "password_hash" not in data

    async def test_get_me_includes_main_photo_url_when_approved_photo_exists(
        self, client: AsyncClient, db_session, mock_verification_code
    ):
        """GET /users/me exposes the user's main photo (approved) as a public URL."""
        from app.core.config import settings
        from tests.photo_visibility_helper import grant_approved_photo

        result = await register_user_full(client, mock_verification_code)
        token = result["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        user_id = result["user"]["id"]

        # No approved photo yet → null.
        res = await client.get(USERS_ME_URL, headers=headers)
        assert res.status_code == 200
        assert res.json()["main_photo_url"] is None

        await grant_approved_photo(user_id)

        # Bypass the Redis user-profile cache + the shared session's identity map
        # (photos were loaded as empty during registration).
        import app.core.redis as redis_module
        from app.core.cache import key_user_profile

        db_session.expire_all()
        await redis_module.redis_client.delete(key_user_profile(user_id))

        res = await client.get(USERS_ME_URL, headers=headers)
        assert res.status_code == 200
        assert res.json()["main_photo_url"] == (
            f"{settings.S3_PUBLIC_BASE_URL}/users/{user_id}/test.jpg"
        )

    async def test_get_me_with_minimal_profile(self, client: AsyncClient, mock_verification_code):
        """Should handle users with minimal profile data."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("minimal@example.com"),
            complete_payload=MINIMAL_PROFILE_PAYLOAD,
        )
        token = result["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()

        assert data["name"] == "Minimal User"
        assert data["gender"] == "female"
        assert data["age"] == calculate_age("2000-01-01")
        assert data["is_profile_complete"] is True
        assert data["lat"] == 35.6892
        assert data["lng"] == 51.3890

        assert data.get("bio") is None
        assert data.get("height") is None
        assert data.get("weight") is None
        assert data.get("body_type") is None
        assert data.get("relationship_status") is None
        assert data.get("country") is None
        assert data.get("province") is None
        assert data.get("city") is None

    async def test_get_me_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.get(USERS_ME_URL)
        assert res.status_code == 401

    async def test_get_me_with_invalid_token(self, client: AsyncClient):
        """Should return 401 with invalid token."""
        headers = {"Authorization": "Bearer invalid-token"}
        res = await client.get(USERS_ME_URL, headers=headers)
        assert res.status_code == 401


# =============================================================================
# PUT /api/v1/users/me
# =============================================================================


class TestUpdateMe:
    async def test_update_name_success(self, client: AsyncClient, mock_verification_code):
        """Should update user name."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"name": "New Name"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "New Name"

    async def test_update_bio_success(self, client: AsyncClient, mock_verification_code):
        """Should update user bio."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"bio": "This is my updated bio"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["bio"] == "This is my updated bio"

    async def test_update_gender_success(self, client: AsyncClient, mock_verification_code):
        """Should update gender."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"gender": "female"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["gender"] == "female"

    async def test_update_sexual_orientation_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update sexual orientation."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"sexual_orientation": "bisexual"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["sexual_orientation"] == "bisexual"

    async def test_update_body_type_success(self, client: AsyncClient, mock_verification_code):
        """Should update body type."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"body_type": "slim"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["body_type"] == "slim"

    async def test_update_relationship_status_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update relationship status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"relationship_status": "divorced"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["relationship_status"] == "divorced"

    async def test_update_living_situation_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update living situation."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"living_situation": "with_partner"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["living_situation"] == "with_partner"

    async def test_update_children_status_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update children status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"children_status": "want_children"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["children_status"] == "want_children"

    async def test_update_smoking_success(self, client: AsyncClient, mock_verification_code):
        """Should update smoking status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"smoking": "regularly"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["smoking"] == "regularly"

    async def test_update_drinking_success(self, client: AsyncClient, mock_verification_code):
        """Should update drinking status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"drinking": "regularly"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["drinking"] == "regularly"

    async def test_update_education_success(self, client: AsyncClient, mock_verification_code):
        """Should update education."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"education": "master"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["education"] == "master"

    async def test_update_workplace_success(self, client: AsyncClient, mock_verification_code):
        """Should update workplace."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"workplace": "Google"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["workplace"] == "Google"

    async def test_update_religion_success(self, client: AsyncClient, mock_verification_code):
        """Should update religion."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"religion": "christian"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["religion"] == "christian"

    async def test_update_ethnicity_success(self, client: AsyncClient, mock_verification_code):
        """Should update ethnicity."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"ethnicity": "kurdish"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ethnicity"] == "kurdish"

    async def test_update_political_orientation_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update political orientation."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"political_orientation": "conservative"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["political_orientation"] == "conservative"

    async def test_update_languages_success(self, client: AsyncClient, mock_verification_code):
        """Should update languages."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"languages": ["persian", "english", "french"]},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["languages"] == ["persian", "english", "french"]

    async def test_update_height_weight_success(self, client: AsyncClient, mock_verification_code):
        """Should update height and weight."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"height": 175, "weight": 70},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["height"] == 175
        assert data["weight"] == 70

    async def test_update_multiple_fields_success(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should update multiple fields at once."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        update_payload = {
            "name": "Updated Name",
            "bio": "Updated bio",
            "gender": "female",
            "sexual_orientation": "pansexual",
            "height": 170,
            "weight": 60,
            "body_type": "slim",
            "relationship_status": "divorced",
            "living_situation": "with_roommate",
            "children_status": "dont_want_children",
            "smoking": "occasionally",
            "drinking": "never",
            "education": "phd",
            "workplace": "Microsoft",
            "religion": "atheist",
            "ethnicity": "persian",
            "political_orientation": "liberal",
            "languages": ["persian", "english", "spanish"],
        }

        res = await client.put(USERS_ME_URL, json=update_payload, headers=headers)
        assert res.status_code == 200
        data = res.json()

        for field, value in update_payload.items():
            assert data.get(field) == value, f"Field {field} mismatch"

    async def test_update_invalid_gender(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid gender."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"gender": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_sexual_orientation(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should reject invalid sexual orientation."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"sexual_orientation": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_body_type(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid body type."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"body_type": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_relationship_status(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should reject invalid relationship status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"relationship_status": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_living_situation(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should reject invalid living situation."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"living_situation": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_children_status(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should reject invalid children status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"children_status": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_smoking(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid smoking status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"smoking": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_drinking(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid drinking status."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"drinking": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_education(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid education level."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"education": "invalid"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_height(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid height."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"height": 300},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_weight(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid weight."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"weight": 400},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_empty_body(self, client: AsyncClient, mock_verification_code):
        """Should reject empty update."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={},
            headers=headers,
        )
        assert res.status_code == 400
        assert "No fields to update" in res.json()["detail"]

    async def test_update_new_profile_fields(self, client: AsyncClient, mock_verification_code):
        """Should update here_for, pets, workout_frequency and zodiac_sign."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={
                "here_for": "long_term_relationship",
                "pets": "cat",
                "workout_frequency": "regularly",
                "zodiac_sign": "leo",
            },
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["here_for"] == "long_term_relationship"
        assert data["pets"] == "cat"
        assert data["workout_frequency"] == "regularly"
        assert data["zodiac_sign"] == "leo"

    async def test_update_invalid_here_for(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid here_for value."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"here_for": "invalid_value"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_pets(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid pets value."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"pets": "dragon"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_invalid_zodiac_sign(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid zodiac sign."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"zodiac_sign": "ophiuchus"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.put(USERS_ME_URL, json={"name": "New Name"})
        assert res.status_code == 401


# =============================================================================
# DELETE /api/v1/users/me  (account deletion with OTP + 30-day grace)
# =============================================================================


class TestDeleteMe:
    async def test_delete_me_success(
        self, client: AsyncClient, mock_verification_code, mock_delete_code
    ):
        """Should soft-delete: deactivate, set deleted_at, lock the phone."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("delete_test@example.com"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        await mock_delete_code(user_id)
        res = await _delete_me(client, headers, {"code": "123456", "reason": "found a partner"})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["message"] == "Account deletion scheduled"
        assert "deletion_scheduled_for" in data

        # Account is deactivated — old token no longer grants access.
        get_res = await client.get(USERS_ME_URL, headers=headers)
        assert get_res.status_code == 403

    async def test_request_delete_code_sends_sms(self, client: AsyncClient, mock_verification_code):
        """POST /users/me/delete-code should send an SMS and store a code."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("delete_code_sms@example.com"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        with patch(
            "app.api.v1.endpoints.users.send_verification_code", new_callable=AsyncMock
        ) as mock_send:
            res = await client.post(USERS_ME_DELETE_CODE_URL, headers=headers)
            assert res.status_code == 204
            assert mock_send.await_count == 1

            cooldown = await client.post(USERS_ME_DELETE_CODE_URL, headers=headers)
            assert cooldown.status_code == 429
            assert "Please wait" in cooldown.json()["detail"]
            assert mock_send.await_count == 1

    async def test_delete_full_flow_uses_real_code(
        self, client: AsyncClient, mock_verification_code
    ):
        """End-to-end: request code → delete with the actual stored code."""
        import json

        import app.core.redis as redis_module

        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("delete_full_flow@example.com"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        with patch("app.api.v1.endpoints.users.send_verification_code", new_callable=AsyncMock):
            await client.post(USERS_ME_DELETE_CODE_URL, headers=headers)

        raw = await redis_module.redis_client.get(f"delete_verify:{user_id}")
        assert raw is not None
        code = json.loads(raw)["code"]

        res = await _delete_me(client, headers, {"code": code})
        assert res.status_code == 200

    async def test_delete_requires_code(self, client: AsyncClient, mock_verification_code):
        """DELETE without a confirmation code should be rejected."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("delete_req_code@example.com"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await _delete_me(client, headers, {})
        assert res.status_code == 422

    async def test_delete_wrong_code(self, client: AsyncClient, mock_verification_code):
        """DELETE with an invalid code should be rejected and not delete."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("delete_wrong_code@example.com"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        # No code stored in Redis → wrong/expired.
        res = await _delete_me(client, headers, {"code": "999999"})
        assert res.status_code == 400

        get_res = await client.get(USERS_ME_URL, headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["is_active"] is True

    async def test_delete_me_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await _delete_me(client, headers={})
        assert res.status_code == 401


# =============================================================================
# Deletion grace window: restore / purge
# =============================================================================


class TestAccountDeletionGrace:
    async def test_deleted_account_restored_within_grace(
        self, client, mock_verification_code, mock_delete_code
    ):
        """Logging in within the 30-day grace window restores the account."""
        phone = _phone("restore_test@example.com")
        result = await register_user_full_custom(client, mock_verification_code, phone=phone)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        await mock_delete_code(user_id)
        res = await _delete_me(client, headers, {"code": "123456"})
        assert res.status_code == 200

        # Same-phone login → restored, not a new account.
        await mock_verification_code(phone, VALID_CODE)
        login_res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
        assert login_res.status_code == 200, login_res.text
        data = login_res.json()
        assert data["is_new_user"] is False
        assert data["account_restored"] is True
        assert data["user"]["is_active"] is True

        # New tokens work on /users/me.
        new_headers = {"Authorization": f"Bearer {data['access_token']}"}
        get_res = await client.get(USERS_ME_URL, headers=new_headers)
        assert get_res.status_code == 200

    async def test_deleted_account_past_grace_blocked(
        self, client, db_session, mock_verification_code, mock_delete_code
    ):
        """Logging in after the grace window (before the sweep runs) is blocked."""
        from sqlalchemy import select

        from app.models.user import User

        phone = _phone("past_grace@example.com")
        result = await register_user_full_custom(client, mock_verification_code, phone=phone)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        await mock_delete_code(user_id)
        res = await _delete_me(client, headers, {"code": "123456"})
        assert res.status_code == 200

        # Backdate deleted_at past the grace window.
        user = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
        user.deleted_at = datetime.now(UTC) - timedelta(days=40)
        await db_session.commit()

        await mock_verification_code(phone, VALID_CODE)
        login_res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
        assert login_res.status_code == 403
        assert "finalized" in login_res.json()["detail"]

    async def test_purge_deleted_accounts(
        self, client, db_session, mock_verification_code, mock_delete_code
    ):
        """Past-grace accounts are hard-deleted by the purge task (incl. cascade)."""
        from sqlalchemy import select

        from app.models.user import User
        from app.models.user_profile import UserProfile

        phone = _phone("purge_test@example.com")
        result = await register_user_full_custom(client, mock_verification_code, phone=phone)
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        await mock_delete_code(user_id)
        res = await _delete_me(client, headers, {"code": "123456"})
        assert res.status_code == 200

        # Backdate past the grace window.
        user = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
        user.deleted_at = datetime.now(UTC) - timedelta(days=40)
        await db_session.commit()

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.tasks.cleanup import _purge_deleted_accounts
        from tests.conftest import make_engine

        # Build a session factory on a fresh, loop-local engine so the purge
        # never touches the global AsyncSessionLocal engine (created at import
        # time on a different event loop — the CI-only "attached to a different
        # loop" failure).
        purge_engine = make_engine()
        purge_factory = async_sessionmaker(
            purge_engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            with patch(
                "app.services.photo_service.PhotoService.delete_photo", new_callable=AsyncMock
            ) as mock_del:
                result = await _purge_deleted_accounts(session_factory=purge_factory)
        finally:
            await purge_engine.dispose()
        assert result == {"purged": 1}

        # User + cascade-deleted profile are gone.
        assert (
            await db_session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none() is None
        assert (
            await db_session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none() is None

        # The phone is free again → brand-new account.
        await mock_verification_code(phone, VALID_CODE)
        res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
        assert res.status_code == 200
        assert res.json()["is_new_user"] is True


# =============================================================================
# POST /api/v1/users/me/location
# =============================================================================


class TestUpdateLocation:
    async def test_update_location_success(self, client: AsyncClient, mock_verification_code):
        """Should update user location with lat/lng."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": 35.6892, "lng": 51.3890},
            headers=headers,
        )
        assert res.status_code == 204

        get_res = await client.get(USERS_ME_URL, headers=headers)
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["lat"] == 35.6892
        assert data["lng"] == 51.3890
        assert data["location_manual"] is False

    async def test_update_location_with_negative_coordinates(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should handle negative coordinates correctly."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": -33.8688, "lng": 151.2093},
            headers=headers,
        )
        assert res.status_code == 204

        get_res = await client.get(USERS_ME_URL, headers=headers)
        data = get_res.json()
        assert data["lat"] == -33.8688
        assert data["lng"] == 151.2093

    async def test_update_location_invalid_lat(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid latitude."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": 100, "lng": 51.3890},
            headers=headers,
        )
        assert res.status_code == 400

    async def test_update_location_invalid_lng(self, client: AsyncClient, mock_verification_code):
        """Should reject invalid longitude."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": 35.6892, "lng": 200},
            headers=headers,
        )
        assert res.status_code == 400

    async def test_update_location_missing_params(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should reject missing parameters."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": 35.6892},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_update_location_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.post(
            USERS_ME_LOCATION_URL,
            params={"lat": 35.6892, "lng": 51.3890},
        )
        assert res.status_code == 401

    async def test_subthreshold_location_ping_skips_coordinate_write_and_geocoding(
        self, client: AsyncClient, mock_verification_code
    ):
        result = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("location_subthreshold")
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        with patch(
            "app.api.v1.endpoints.users.LocationService.reverse_geocode",
            new_callable=AsyncMock,
        ) as reverse_geocode:
            response = await client.post(
                USERS_ME_LOCATION_URL,
                params={"lat": 35.6893, "lng": 51.3890},
                headers=headers,
            )

        assert response.status_code == 204
        reverse_geocode.assert_not_awaited()
        profile = (await client.get(USERS_ME_URL, headers=headers)).json()
        assert profile["lat"] == COMPLETE_PROFILE_PAYLOAD["lat"]

    async def test_location_update_only_autofills_present_reverse_geocode_fields(
        self, client: AsyncClient, mock_verification_code
    ):
        result = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("location_partial_geocode")
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        with patch(
            "app.api.v1.endpoints.users.LocationService.reverse_geocode",
            new_callable=AsyncMock,
            return_value={"country": "Canada", "city": "Toronto"},
        ) as reverse_geocode:
            response = await client.post(
                USERS_ME_LOCATION_URL,
                params={"lat": 43.6532, "lng": -79.3832},
                headers=headers,
            )

        assert response.status_code == 204
        reverse_geocode.assert_awaited_once_with(43.6532, -79.3832)
        profile = (await client.get(USERS_ME_URL, headers=headers)).json()
        assert profile["country"] == "Canada"
        assert profile["city"] == "Toronto"
        assert profile["province"] == COMPLETE_PROFILE_PAYLOAD["province"]

    async def test_location_update_preserves_manual_location_text(
        self, client: AsyncClient, mock_verification_code
    ):
        result = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("location_manual_text")
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        text_update = await client.patch(
            USERS_ME_LOCATION_TEXT_URL,
            json={"country": "Japan", "province": "Tokyo", "city": "Tokyo"},
            headers=headers,
        )
        assert text_update.status_code == 200

        with patch(
            "app.api.v1.endpoints.users.LocationService.reverse_geocode",
            new_callable=AsyncMock,
        ) as reverse_geocode:
            response = await client.post(
                USERS_ME_LOCATION_URL,
                params={"lat": 35.6762, "lng": 139.6503},
                headers=headers,
            )

        assert response.status_code == 204
        reverse_geocode.assert_not_awaited()
        profile = (await client.get(USERS_ME_URL, headers=headers)).json()
        assert (profile["country"], profile["province"], profile["city"]) == (
            "Japan",
            "Tokyo",
            "Tokyo",
        )


# =============================================================================
# PATCH /api/v1/users/me/location-text
# =============================================================================


class TestUpdateLocationText:
    async def test_update_location_text_success(self, client: AsyncClient, mock_verification_code):
        """Should update location with text fields."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.patch(
            USERS_ME_LOCATION_TEXT_URL,
            json={
                "country": "Iran",
                "province": "Isfahan",
                "city": "Isfahan",
            },
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["country"] == "Iran"
        assert data["province"] == "Isfahan"
        assert data["city"] == "Isfahan"
        assert data["location_manual"] is True

    async def test_update_location_text_partial(self, client: AsyncClient, mock_verification_code):
        """Should update only provided text fields."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.patch(
            USERS_ME_LOCATION_TEXT_URL,
            json={"province": "Fars"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["province"] == "Fars"
        assert data["location_manual"] is True

        get_res = await client.get(USERS_ME_URL, headers=headers)
        get_data = get_res.json()
        assert get_data["country"] == "Iran"
        assert get_data["city"] == "Tehran"

    async def test_update_location_text_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.patch(
            USERS_ME_LOCATION_TEXT_URL,
            json={"country": "Iran", "province": "Tehran", "city": "Tehran"},
        )
        assert res.status_code == 401

    async def test_update_location_text_empty(self, client: AsyncClient, mock_verification_code):
        """Should handle empty update gracefully."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.patch(
            USERS_ME_LOCATION_TEXT_URL,
            json={},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["location_manual"] is False


# =============================================================================
# Additional Tests
# =============================================================================


class TestUserSettings:
    async def test_settings_created_on_registration(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should create default settings on registration."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        data = res.json()

        settings = data["settings"]
        assert settings["hide_last_seen"] is False
        assert settings["hide_online_status"] is False
        assert settings["push_enabled"] is True
        assert settings["like_notifications"] is True
        assert settings["match_notifications"] is True
        assert settings["message_notifications"] is True
        assert settings["language"] == "fa"
        assert settings["dark_mode"] is False

    async def test_update_settings_persists_values_and_ignores_explicit_null(
        self, client: AsyncClient, mock_verification_code
    ):
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("update_settings_values"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        response = await client.put(
            "/api/v1/users/me/settings",
            json={"hide_last_seen": True, "language": "en", "dark_mode": None},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["hide_last_seen"] is True
        assert response.json()["language"] == "en"
        assert response.json()["dark_mode"] is False

    async def test_update_settings_rejects_empty_payload(self, client, mock_verification_code):
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("update_settings_empty"),
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        response = await client.put(
            "/api/v1/users/me/settings", json={}, headers=headers
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "No fields to update"


class TestUserInterestsAndPrompts:
    async def test_update_interests_replaces_list_and_rejects_unknown_names(
        self, client: AsyncClient, mock_verification_code
    ):
        result = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("update_user_interests")
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        response = await client.put(
            "/api/v1/users/me/interests",
            json={"interests": ["football", "reading"]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["interests"]) == {"football", "reading"}

        invalid = await client.put(
            "/api/v1/users/me/interests",
            json={"interests": ["not-a-seeded-interest"]},
            headers=headers,
        )
        assert invalid.status_code == 400
        assert "not-a-seeded-interest" in invalid.json()["detail"]

        cleared = await client.put(
            "/api/v1/users/me/interests", json={"interests": []}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["interests"] == []

    async def test_update_prompts_replaces_prompt_answers(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        from app.models.prompt import Prompt

        result = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("update_user_prompts")
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        first_prompt = Prompt(question="A favorite place?", language="en", is_active=True)
        second_prompt = Prompt(question="A perfect day?", language="en", is_active=True)
        db_session.add_all([first_prompt, second_prompt])
        await db_session.commit()

        response = await client.put(
            "/api/v1/users/me/prompts",
            json={
                "prompts": [
                    {"prompt_id": str(first_prompt.id), "answer": "The mountains"},
                    {"prompt_id": str(second_prompt.id), "answer": "A long walk"},
                ]
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert {p["answer"] for p in response.json()["prompts"]} == {
            "The mountains",
            "A long walk",
        }

        replaced = await client.put(
            "/api/v1/users/me/prompts",
            json={"prompts": [{"prompt_id": str(second_prompt.id), "answer": "Coffee"}]},
            headers=headers,
        )
        assert replaced.status_code == 200, replaced.text
        assert [p["answer"] for p in replaced.json()["prompts"]] == ["Coffee"]

        cleared = await client.put(
            "/api/v1/users/me/prompts", json={"prompts": []}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["prompts"] == []


class TestPublicProfile:
    async def test_public_profile_returns_serialized_profile_and_reuses_cache(
        self, client: AsyncClient, mock_verification_code
    ):
        viewer = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("public_viewer")
        )
        target = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("public_target")
        )
        viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
        target_headers = {"Authorization": f"Bearer {target['access_token']}"}
        target_id = target["user"]["id"]

        first = await client.get(f"/api/v1/users/{target_id}", headers=viewer_headers)
        assert first.status_code == 200, first.text
        assert first.json()["id"] == target_id
        assert first.json()["name"] == COMPLETE_PROFILE_PAYLOAD["name"]

        # Cached payloads exclude viewer-relative online and distance values.
        from app.core.cache import key_public_profile
        from app.core.redis import redis_client

        raw = await redis_client.get(key_public_profile(UUID(target_id)))
        cached = json.loads(raw)
        assert "distance_km" not in cached["profile"]
        assert "is_online" not in cached["profile"]

        await redis_client.setex(f"online:{target_id}", 60, "1")
        second = await client.get(f"/api/v1/users/{target_id}", headers=viewer_headers)
        assert second.status_code == 200, second.text
        assert second.json()["is_online"] is True
        assert second.json()["distance_km"] is not None

        own_profile = await client.get(f"/api/v1/users/{target_id}", headers=target_headers)
        assert own_profile.status_code == 400
        assert own_profile.json()["detail"] == "Cannot view your own profile here"

    async def test_public_profile_hides_targets_when_blocked_either_way(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        from app.models.block import Block

        viewer = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("blocked_viewer")
        )
        target = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("blocked_target")
        )
        viewer_id, target_id = UUID(viewer["user"]["id"]), UUID(target["user"]["id"])
        headers = {"Authorization": f"Bearer {viewer['access_token']}"}

        db_session.add(Block(blocker_id=viewer_id, blocked_id=target_id))
        await db_session.commit()
        response = await client.get(f"/api/v1/users/{target_id}", headers=headers)
        assert response.status_code == 404

        await db_session.execute(delete(Block).where(Block.blocker_id == viewer_id))
        db_session.add(Block(blocker_id=target_id, blocked_id=viewer_id))
        await db_session.commit()
        response = await client.get(f"/api/v1/users/{target_id}", headers=headers)
        assert response.status_code == 404

    async def test_public_profile_rejects_missing_or_inactive_targets(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        from app.models.user import User

        viewer = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("public_missing_viewer")
        )
        target = await register_user_full_custom(
            client, mock_verification_code, phone=_phone("public_inactive_target")
        )
        headers = {"Authorization": f"Bearer {viewer['access_token']}"}
        target_id = UUID(target["user"]["id"])

        missing = await client.get(f"/api/v1/users/{uuid4()}", headers=headers)
        assert missing.status_code == 404

        target_row = await db_session.get(User, target_id)
        target_row.is_active = False
        await db_session.commit()
        inactive = await client.get(f"/api/v1/users/{target_id}", headers=headers)
        assert inactive.status_code == 404

class TestPremiumStatus:
    async def test_is_premium_field_exists(self, client: AsyncClient, mock_verification_code):
        """Should show is_premium field in response."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        data = res.json()

        assert "is_premium" in data
        assert "premium_until" in data


class TestProfileCompleteness:
    async def test_is_profile_complete_true_after_complete(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should show is_profile_complete as true after completing profile."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        data = res.json()
        assert data["is_profile_complete"] is True

    async def test_is_profile_complete_with_minimal(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should show is_profile_complete as true with minimal required fields."""
        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=_phone("minimal2@example.com"),
            complete_payload=MINIMAL_PROFILE_PAYLOAD,
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        data = res.json()
        assert data["is_profile_complete"] is True


class TestEdgeCases:
    async def test_update_with_unicode_characters(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should handle Unicode characters properly."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        update_payload = {
            "name": "احسان محمدی",
            "bio": "سلام! من تست‌کننده هستم. 😊",
            "workplace": "شرکت فناوری",
        }

        res = await client.put(USERS_ME_URL, json=update_payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "احسان محمدی"
        assert data["bio"] == "سلام! من تست‌کننده هستم. 😊"
        assert data["workplace"] == "شرکت فناوری"

    async def test_update_name_with_emoji(self, client: AsyncClient, mock_verification_code):
        """Should handle emoji in name."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.put(
            USERS_ME_URL,
            json={"name": "John 🚀"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "John 🚀"

    async def test_get_me_after_settings_update(self, client: AsyncClient, mock_verification_code):
        """Should reflect settings updates when getting profile."""
        result = await register_user_full(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.get(USERS_ME_URL, headers=headers)
        data = res.json()
        assert data["settings"] is not None

    async def test_duplicate_phone_cannot_register(
        self, client: AsyncClient, mock_verification_code
    ):
        """Re-verifying an existing phone logs in instead of creating a new user."""
        phone = _phone("duplicate@example.com")
        await register_user_full_custom(
            client,
            mock_verification_code,
            phone=phone,
        )

        # Verify-code on the same phone → it's a login, not a new registration.
        await mock_verification_code(phone, VALID_CODE)
        res = await client.post(
            VERIFY_CODE_URL,
            json={"phone": phone, "code": VALID_CODE},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_new_user"] is False


class TestAccountReactivation:
    async def test_deleted_account_restores_via_login(
        self, client: AsyncClient, mock_verification_code, mock_delete_code
    ):
        """Logging in within the grace window restores a deleted account."""
        phone = _phone("delete_test2@example.com")

        result = await register_user_full_custom(
            client,
            mock_verification_code,
            phone=phone,
        )
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        user_id = result["user"]["id"]

        await mock_delete_code(user_id)
        res = await _delete_me(client, headers, {"code": VALID_CODE})
        assert res.status_code == 200

        await mock_verification_code(phone, VALID_CODE)
        login_res = await client.post(
            VERIFY_CODE_URL,
            json={"phone": phone, "code": VALID_CODE},
        )
        assert login_res.status_code == 200
        assert login_res.json()["is_new_user"] is False
