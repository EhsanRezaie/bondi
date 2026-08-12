import pytest
from httpx import AsyncClient

from app.core.config import settings as app_settings

# ============ URL Constants ============
REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
LOGIN_URL = "/api/v1/auth/login"

VALID_CODE = "123456"

MALE_PROFILE = {
    "name": "Swipe Male",
    "birth_date": "1999-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}

FEMALE_PROFILE = {
    "name": "Swipe Female",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(
    client: AsyncClient,
    mock_verification_code,
    email: str,
    profile: dict,
    password: str = "strongpass123",
) -> dict:
    """Register a user via 3-step flow and return tokens."""
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200

    await mock_verification_code(email, VALID_CODE)

    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email,
        "code": VALID_CODE,
        "password": password,
    })
    assert res.status_code == 200
    tokens = res.json()

    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=profile,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 200
    return res.json()


async def register_male(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "swipe_male@example.com", MALE_PROFILE
    )


async def register_female(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "swipe_female@example.com", FEMALE_PROFILE
    )


# =============================================================================
# POST /api/v1/swipes
# =============================================================================

class TestSwipe:

    async def test_swipe_like_success(self, client, mock_verification_code):
        """Should successfully like another user."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["matched"] is False
        assert data["message"] == "Swiped successfully"

    async def test_swipe_pass_success(self, client, mock_verification_code):
        """Should successfully pass on a user."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "pass"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["matched"] is False

    async def test_mutual_like_creates_match(self, client, mock_verification_code):
        """Should create match when both users like each other."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        # Male likes female
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        # Female likes male (mutual)
        res = await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["matched"] is True
        assert data["match_id"] is not None
        assert "You matched!" in data["message"]

    async def test_cannot_swipe_self(self, client, mock_verification_code):
        """Should not allow swiping on yourself."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "Cannot swipe on yourself" in res.json()["detail"]

    async def test_cannot_swipe_twice_same_direction(self, client, mock_verification_code):
        """Should not allow swiping on same user twice in the same direction."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        # First swipe
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )

        # Second swipe — same user, same direction
        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "Already swiped" in res.json()["detail"]

    async def test_swipe_direction_change_allowed(self, client, mock_verification_code):
        """A reverted (previously passed) card can be liked: pass -> like must succeed."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        # Pass first
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "pass"},
            headers=headers,
        )

        # Like the same user (client-side revert) — allowed now
        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["matched"] is False

        # Stats reflect the final direction (like), not the old pass
        stats = await client.get(f"{SWIPE_URL}/stats", headers=headers)
        data = stats.json()
        assert data["total_likes_sent"] == 1
        assert data["total_passes_sent"] == 0

    async def test_swipe_nonexistent_user(self, client, mock_verification_code):
        """Should return 404 when swiping on non-existent user."""
        from uuid import uuid4
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": str(uuid4()), "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    async def test_swipe_requires_auth(self, client):
        """Should return 401 without token."""
        from uuid import uuid4
        res = await client.post(
            SWIPE_URL,
            json={"user_id": str(uuid4()), "direction": "like"},
        )
        assert res.status_code == 401

    async def test_swipe_invalid_direction(self, client, mock_verification_code):
        """Should accept any direction string (no server-side validation)."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "invalid"},
            headers=headers,
        )
        assert res.status_code == 200

    async def test_swipe_like_returns_remaining_likes(self, client, mock_verification_code):
        """Should return likes_remaining_today after a like swipe."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert "likes_remaining_today" in data
        assert data["likes_remaining_today"] is None  # null = unlimited (welcome premium bonus)

    async def test_swipe_pass_returns_no_remaining(self, client, mock_verification_code):
        """Should not return likes_remaining_today for a pass."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "pass"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert "likes_remaining_today" in data
        assert data["likes_remaining_today"] is None


# =============================================================================
# GET /api/v1/swipes/stats
# =============================================================================

class TestSwipeStats:

    async def test_stats_initial(self, client, mock_verification_code):
        """Should return zero stats when no swipes have been made."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.get(f"{SWIPE_URL}/stats", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total_likes_sent"] == 0
        assert data["total_passes_sent"] == 0
        assert data["total_matches"] == 0

    async def test_stats_after_swipes(self, client, mock_verification_code):
        """Should reflect swipes made."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=headers,
        )

        res = await client.get(f"{SWIPE_URL}/stats", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total_likes_sent"] == 1
        assert data["total_passes_sent"] == 0
        assert data["total_matches"] == 0

    async def test_stats_after_match(self, client, mock_verification_code):
        """Should show match in stats after mutual like."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )

        res = await client.get(f"{SWIPE_URL}/stats", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total_likes_sent"] == 1
        assert data["total_matches"] == 1

    async def test_stats_requires_auth(self, client):
        """Should return 401 without token."""
        res = await client.get(f"{SWIPE_URL}/stats")
        assert res.status_code == 401

    async def test_stats_contains_all_fields(self, client, mock_verification_code):
        """Should return all expected stat fields."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.get(f"{SWIPE_URL}/stats", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "daily_likes_remaining" in data
        assert "is_unlimited" in data
        assert "total_likes_sent" in data
        assert "total_passes_sent" in data
        assert "total_matches" in data
        assert "ads_watched_today" in data
        assert "max_ads_per_day" in data

    async def test_stats_types_are_correct(self, client, mock_verification_code):
        """All SwipeStatsResponse fields should have correct types."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.get(f"{SWIPE_URL}/stats", headers=headers)
        assert res.status_code == 200
        data = res.json()

        assert isinstance(data["daily_likes_remaining"], int)
        assert isinstance(data["is_unlimited"], bool)
        assert isinstance(data["total_likes_sent"], int)
        assert isinstance(data["total_passes_sent"], int)
        assert isinstance(data["total_matches"], int)
        assert isinstance(data["ads_watched_today"], int)
        assert isinstance(data["max_ads_per_day"], int)
        assert data["is_unlimited"] is True  # welcome bonus
