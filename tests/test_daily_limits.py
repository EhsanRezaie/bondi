
# tests/test_daily_limits.py
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
REWARDS_LIMITS_URL = "/api/v1/rewards/my-limits"
SWIPE_STATS_URL = "/api/v1/swipes/stats"

VALID_EMAIL = _phone("daily@example.com")
VALID_EMAIL2 = _phone("daily2@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD = {
    "name": "Daily User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 180,
    "weight": 75,
}

COMPLETE_PROFILE_PAYLOAD2 = {
    "name": "Daily User 2",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio 2",
    "height": 165,
    "weight": 60,
}


async def register_user_full(
    client: AsyncClient,
    phone: str,
    complete_payload: dict,
    mock_verification_code
) -> dict:
    """Complete full registration via phone OTP - returns user data with tokens."""
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


async def register_and_get_headers(
    client: AsyncClient,
    phone: str,
    complete_payload: dict,
    mock_verification_code
) -> tuple[dict, str]:
    """Register a user via phone OTP and return headers with user_id."""
    result = await register_user_full(client, phone, complete_payload, mock_verification_code)
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    user_id = result["user"]["id"]
    return headers, user_id


class TestDailyLimits:
    """Test daily limits enforcement"""

    async def test_premium_user_unlimited_likes(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Premium user should have unlimited likes (-1)."""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code
        )
        
        res = await client.get(REWARDS_LIMITS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        
        # Welcome bonus makes user premium
        assert body["likes_remaining_today"] == -1
        assert body["chats_remaining_today"] == -1

    async def test_cannot_swipe_on_self(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Swiping on yourself should return 400."""
        headers, user_id = await register_and_get_headers(
            client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code
        )
        
        res = await client.post(
            SWIPE_URL,
            json={"user_id": user_id, "direction": "like"},
            headers=headers
        )
        assert res.status_code == 400
        assert "Cannot swipe on yourself" in res.json()["detail"]

    async def test_cannot_swipe_twice_on_same_user(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot swipe twice on the same user."""
        # Register two users
        user1_headers, user1_id = await register_and_get_headers(
            client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code
        )
        user2_headers, user2_id = await register_and_get_headers(
            client, VALID_EMAIL2, COMPLETE_PROFILE_PAYLOAD2, mock_verification_code
        )
        
        # First swipe
        res1 = await client.post(
            SWIPE_URL,
            json={"user_id": user2_id, "direction": "like"},
            headers=user1_headers
        )
        assert res1.status_code == 200
        
        # Second swipe - should fail
        res2 = await client.post(
            SWIPE_URL,
            json={"user_id": user2_id, "direction": "like"},
            headers=user1_headers
        )
        assert res2.status_code == 400
        assert "Already swiped" in res2.json()["detail"]

    async def test_swipe_stats_returns_correct_structure(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Swipe stats endpoint should return correct structure."""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code
        )
        
        res = await client.get(SWIPE_STATS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        
        expected_fields = [
            "daily_likes_remaining", "is_unlimited",
            "total_likes_sent", "total_passes_sent", "total_matches",
            "total_messages",
            "ads_watched_today", "max_ads_per_day"
        ]
        for field in expected_fields:
            assert field in body