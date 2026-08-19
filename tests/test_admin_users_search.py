import pytest
from httpx import AsyncClient
import uuid as uuid_lib

from app.core.config import settings

ADMIN_USERS_URL = "/api/v1/admin/users"
ADMIN_KEY = settings.ADMIN_SECRET_KEY

VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
VALID_CODE = "123456"

BASE_PROFILE = {
    "name": "Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "bio": "Looking for fun adventures in Tehran",
    "height": 180,
    "weight": 75,
    "body_type": "average",
    "relationship_status": "single",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Acme Corp",
    "religion": "none",
    "ethnicity": "persian",
    "lat": 35.6892,
    "lng": 51.3890,
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
    "languages": ["fa", "en"],
}


async def register_user(client: AsyncClient, db_session, email: str, mock_verification_code, profile: dict | None = None) -> dict:
    """Create a user via the phone-OTP flow, keeping the email set so the
    admin search tests can keep filtering/asserting by email."""
    from app.models.user import User

    # Pre-create the account with phone + email so verify-code logs in to it
    # (email stays attached for the admin search assertions).
    user = User(
        id=uuid_lib.uuid4(),
        phone=f"+9891{uuid_lib.uuid4().int % 10_000_000_000:010d}",
        email=email,
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="phone_verified",
    )
    db_session.add(user)
    await db_session.flush()

    await mock_verification_code(user.phone, VALID_CODE)
    res = await client.post(VERIFY_CODE_URL, json={"phone": user.phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    payload = {**BASE_PROFILE, **(profile or {})}
    if "name" not in payload:
        payload["name"] = email.split("@")[0]
    res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _emails(body: dict) -> set:
    return {u["email"] for u in body["users"]}


class TestAdminSearchFilters:
    """Tests for the extended server-side admin search filters."""

    async def test_list_returns_minimal_fields(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "minimal@example.com", mock_verification_code)
        res = await client.get(ADMIN_USERS_URL, params={"limit": 5}, headers={"X-Admin-Key": ADMIN_KEY})
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        user = body["users"][0]
        for field in ("id", "name", "email", "age", "gender", "city", "is_active", "is_premium"):
            assert field in user
        for heavy in ("photos", "interests", "bio", "total_likes_sent", "total_matches"):
            assert heavy not in user

    async def test_filter_height_range(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "tall@example.com", mock_verification_code, {"height": 195})
        await register_user(client, db_session, "short@example.com", mock_verification_code, {"height": 150})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"height_min": 180, "height_max": 200},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "tall@example.com" in emails
        assert "short@example.com" not in emails

    async def test_filter_weight_range(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "heavy@example.com", mock_verification_code, {"weight": 120})
        await register_user(client, db_session, "light@example.com", mock_verification_code, {"weight": 50})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"weight_min": 100, "weight_max": 150},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "heavy@example.com" in emails
        assert "light@example.com" not in emails

    async def test_filter_body_type(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "slim@example.com", mock_verification_code, {"body_type": "slim"})
        await register_user(client, db_session, "curvy@example.com", mock_verification_code, {"body_type": "curvy"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"body_type": "slim"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "slim@example.com" in emails
        assert "curvy@example.com" not in emails

    async def test_filter_relationship_status(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "single@example.com", mock_verification_code, {"relationship_status": "single"})
        await register_user(client, db_session, "divorced@example.com", mock_verification_code, {"relationship_status": "divorced"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"relationship_status": "divorced"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "divorced@example.com" in emails
        assert "single@example.com" not in emails

    async def test_filter_education(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "phd@example.com", mock_verification_code, {"education": "phd"})
        await register_user(client, db_session, "hs@example.com", mock_verification_code, {"education": "high_school"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"education": "phd"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "phd@example.com" in emails
        assert "hs@example.com" not in emails

    async def test_filter_religion(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "islam@example.com", mock_verification_code, {"religion": "islam"})
        await register_user(client, db_session, "none@example.com", mock_verification_code, {"religion": "none"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"religion": "islam"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "islam@example.com" in emails
        assert "none@example.com" not in emails

    async def test_filter_ethnicity(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "kurd@example.com", mock_verification_code, {"ethnicity": "kurdish"})
        await register_user(client, db_session, "persian@example.com", mock_verification_code, {"ethnicity": "persian"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"ethnicity": "kurdish"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "kurd@example.com" in emails
        assert "persian@example.com" not in emails

    async def test_filter_political_orientation(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "lib@example.com", mock_verification_code, {"political_orientation": "liberal"})
        await register_user(client, db_session, "cons@example.com", mock_verification_code, {"political_orientation": "conservative"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"political_orientation": "conservative"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "cons@example.com" in emails
        assert "lib@example.com" not in emails

    async def test_filter_smoking(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "never@example.com", mock_verification_code, {"smoking": "never"})
        await register_user(client, db_session, "regular@example.com", mock_verification_code, {"smoking": "regularly"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"smoking": "regularly"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "regular@example.com" in emails
        assert "never@example.com" not in emails

    async def test_filter_drinking(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "abst@example.com", mock_verification_code, {"drinking": "never"})
        await register_user(client, db_session, "soc@example.com", mock_verification_code, {"drinking": "socially"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"drinking": "socially"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "soc@example.com" in emails
        assert "abst@example.com" not in emails

    async def test_filter_country_and_province(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "iran@example.com", mock_verification_code, {"country": "Iran", "province": "Tehran"})
        await register_user(client, db_session, "fr@example.com", mock_verification_code, {"country": "France", "province": "Ile-de-France"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"country": "iran"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "iran@example.com" in emails
        assert "fr@example.com" not in emails

        res = await client.get(
            ADMIN_USERS_URL,
            params={"province": "ile-de"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "fr@example.com" in emails
        assert "iran@example.com" not in emails

    async def test_filter_languages_jsonb(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "en@example.com", mock_verification_code, {"languages": ["fa", "en"]})
        await register_user(client, db_session, "de@example.com", mock_verification_code, {"languages": ["de"]})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"languages": "en"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "en@example.com" in emails
        assert "de@example.com" not in emails

    async def test_filter_has_photos(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "nophoto@example.com", mock_verification_code)
        res = await client.get(
            ADMIN_USERS_URL,
            params={"has_photos": True},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        emails = _emails(res.json())
        assert "nophoto@example.com" not in emails


class TestAdminSearchSorting:
    """Tests for sort_by/sort_order on the admin user list."""

    async def test_sort_by_name_asc(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "alpha@example.com", mock_verification_code, {"name": "Alpha User"})
        await register_user(client, db_session, "beta@example.com", mock_verification_code, {"name": "Beta User"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "name", "sort_order": "asc", "search": "alpha@example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "alpha@example.com"

    async def test_sort_by_name_desc(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "alpha@example.com", mock_verification_code, {"name": "Alpha User"})
        await register_user(client, db_session, "beta@example.com", mock_verification_code, {"name": "Beta User"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "name", "sort_order": "desc", "search": "beta@example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "beta@example.com"

    async def test_sort_by_age_asc(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "older@example.com", mock_verification_code, {"birth_date": "1970-01-01"})
        await register_user(client, db_session, "younger@example.com", mock_verification_code, {"birth_date": "2000-01-01"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "age", "sort_order": "asc", "search": "example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "older@example.com"

    async def test_sort_by_age_desc(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "older@example.com", mock_verification_code, {"birth_date": "1970-01-01"})
        await register_user(client, db_session, "younger@example.com", mock_verification_code, {"birth_date": "2000-01-01"})
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "age", "sort_order": "desc", "search": "example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "younger@example.com"

    async def test_sort_by_email(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "zz@example.com", mock_verification_code)
        await register_user(client, db_session, "aa@example.com", mock_verification_code)
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "email", "sort_order": "asc", "search": "example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "aa@example.com"

    async def test_sort_by_created_at_default_desc(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "older@example.com", mock_verification_code)
        await register_user(client, db_session, "newer@example.com", mock_verification_code)
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "created_at", "sort_order": "desc", "search": "example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["users"]
        assert body["users"][0]["email"] == "newer@example.com"

    async def test_sort_by_invalid_falls_back_to_created_at(self, client: AsyncClient, db_session, mock_verification_code):
        await register_user(client, db_session, "fallback@example.com", mock_verification_code)
        res = await client.get(
            ADMIN_USERS_URL,
            params={"sort_by": "not_a_real_column", "sort_order": "desc", "search": "fallback@example.com"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert res.status_code == 200
        assert res.json()["users"]