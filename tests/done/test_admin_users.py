import pytest
from httpx import AsyncClient
from app.core.config import settings

ADMIN_USERS_URL = "/api/v1/admin/users"
ADMIN_KEY = settings.ADMIN_SECRET_KEY

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"
COMPLETE_PROFILE = {
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


async def register_user(client: AsyncClient, email: str = None, mock_verification_code=None) -> dict:
    if email is None:
        email = "user@example.com"
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text
    if mock_verification_code:
        await mock_verification_code(email, VALID_CODE)
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email, "code": VALID_CODE, "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


class TestAdminUsers:
    """Test admin user management"""

    async def test_admin_list_users_success(self, client: AsyncClient, mock_verification_code):
        """Admin should list all users"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(ADMIN_USERS_URL, headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert "users" in body
        assert "total" in body

    async def test_admin_list_users_search_by_name(self, client: AsyncClient, mock_verification_code):
        """Admin should search users by name"""
        # Create a user with unique name
        await register_user(client, "search_test@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"search": "Test User"},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["users"]) >= 1

    async def test_admin_list_users_search_by_email(self, client: AsyncClient, mock_verification_code):
        """Admin should search users by email"""
        await register_user(client, "search_test@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"search": "search_test@example.com"},
            headers=admin_headers
        )
        assert res.status_code == 200

    async def test_admin_list_users_filter_active(self, client: AsyncClient):
        """Admin should filter active users"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"is_active": True},
            headers=admin_headers
        )
        assert res.status_code == 200

    async def test_admin_list_users_filter_inactive(self, client: AsyncClient):
        """Admin should filter inactive users"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"is_active": False},
            headers=admin_headers
        )
        assert res.status_code == 200

    async def test_admin_list_users_filter_premium(self, client: AsyncClient):
        """Admin should filter premium users"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"is_premium": True},
            headers=admin_headers
        )
        assert res.status_code == 200

    async def test_admin_list_users_pagination(self, client: AsyncClient):
        """Admin should paginate users"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"limit": 5, "offset": 0},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["users"]) <= 5

    async def test_admin_get_user_detail(self, client: AsyncClient, mock_verification_code):
        """Admin should view user details with stats"""
        # Create a user
        user_data = await register_user(client, "detail@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}",
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == str(user_data['user']['id'])
        assert body["email"] == user_data['user']['email']
        assert "total_likes_sent" in body
        assert "total_matches" in body
        assert "total_messages" in body

    async def test_admin_get_nonexistent_user(self, client: AsyncClient):
        """Should return 404 for non-existent user"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_USERS_URL}/00000000-0000-0000-0000-000000000001",
            headers=admin_headers
        )
        assert res.status_code == 404

    async def test_admin_deactivate_user(self, client: AsyncClient, mock_verification_code):
        """Admin should deactivate a user"""
        # Create a user
        user_data = await register_user(client, "deactivate@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.patch(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}",
            json={"is_active": False},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["is_active"] is False

        # User should not be able to login
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "deactivate@example.com", "password": "strongpass123"}
        )
        assert login_res.status_code == 401

    async def test_admin_activate_user(self, client: AsyncClient, mock_verification_code):
        """Admin should activate a deactivated user"""
        # Create a user
        user_data = await register_user(client, "activate@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}

        # Deactivate
        await client.patch(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}",
            json={"is_active": False},
            headers=admin_headers
        )

        # Activate
        res = await client.patch(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}",
            json={"is_active": True},
            headers=admin_headers
        )
        assert res.status_code == 200
        assert res.json()["is_active"] is True

    async def test_admin_grant_premium(self, client: AsyncClient, mock_verification_code):
        """Admin should grant premium days to user"""
        # Create a user
        user_data = await register_user(client, "premium@example.com", mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}/premium",
            json={"days": 30},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["is_premium"] is True
        assert body["premium_until"] is not None

    async def test_admin_delete_user(self, client: AsyncClient, mock_verification_code):
        """Admin should hard delete a user"""
        # Create a user
        user_data = await register_user(client, "delete_me@example.com", mock_verification_code)
        user_id = user_data["user"]["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.delete(f"{ADMIN_USERS_URL}/{user_id}", headers=admin_headers)
        assert res.status_code == 204

        # User should not exist
        get_res = await client.get(f"{ADMIN_USERS_URL}/{user_id}", headers=admin_headers)
        assert get_res.status_code == 404

    async def test_admin_get_user_activity(self, client: AsyncClient, mock_verification_code):
        """Admin should get user activity stats"""
        # Create a user and do some activity
        user_data = await register_user(client, "activity@example.com", mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}

        # Create another user to swipe
        target_data = await register_user(client, "activity_target@example.com", mock_verification_code)

        # Perform a swipe
        await client.post(
            "/api/v1/swipes",
            json={"user_id": target_data["user"]["id"], "direction": "like"},
            headers=user_headers
        )

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}/activity",
            params={"days": 7},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 1
        assert "date" in body[0]
        assert "swipes" in body[0]

    async def test_admin_search_by_bio(self, client: AsyncClient, mock_verification_code):
        """Admin should search users by bio text"""
        await register_user(client, "bio_search@example.com", mock_verification_code)
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"search": "fun adventures in Tehran"},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert any(u["email"] == "bio_search@example.com" for u in body["users"])

    async def test_admin_search_by_id(self, client: AsyncClient, mock_verification_code):
        """Admin should search users by UUID"""
        user_data = await register_user(client, "id_search@example.com", mock_verification_code)
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"id": str(user_data["user"]["id"])},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert any(u["id"] == str(user_data["user"]["id"]) for u in body["users"])

    async def test_admin_filter_by_gender(self, client: AsyncClient, mock_verification_code):
        """Admin should filter users by gender"""
        await register_user(client, "gender@example.com", mock_verification_code)
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"gender": "male"},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["users"]) >= 1
        assert all(u["gender"] == "male" for u in body["users"])

    async def test_admin_filter_by_city(self, client: AsyncClient, mock_verification_code):
        """Admin should filter users by city"""
        await register_user(client, "city@example.com", mock_verification_code)
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"city": "Tehran"},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert any(u["email"] == "city@example.com" for u in body["users"])

    async def test_admin_filter_by_age_range(self, client: AsyncClient, mock_verification_code):
        """Admin should filter users by age range"""
        await register_user(client, "age@example.com", mock_verification_code)  # born 1995 => ~31
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            ADMIN_USERS_URL,
            params={"age_min": 25, "age_max": 35},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert any(u["email"] == "age@example.com" for u in body["users"])

    async def test_admin_user_detail_full_profile(self, client: AsyncClient, mock_verification_code):
        """Admin detail should include full profile, photos, interests and UID"""
        user_data = await register_user(client, "full@example.com", mock_verification_code)
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_USERS_URL}/{user_data['user']['id']}",
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        # UID = the user id
        assert body["id"] == str(user_data["user"]["id"])
        # Full profile fields
        assert body["name"] == "Test User"
        assert body["birth_date"] == "1995-06-15"
        assert body["bio"] == "Looking for fun adventures in Tehran"
        assert body["height"] == 180
        assert body["weight"] == 75
        assert body["relationship_status"] == "single"
        assert body["education"] == "bachelor"
        assert body["workplace"] == "Acme Corp"
        assert body["city"] == "Tehran"
        assert body["languages"] == ["fa", "en"]
        # Relations
        assert "interests" in body
        assert "photos" in body
        assert isinstance(body["photos"], list)

    async def test_admin_users_requires_admin_key(self, client: AsyncClient):
        """Should return 403 without admin key"""
        res = await client.get(ADMIN_USERS_URL)
        assert res.status_code == 403
