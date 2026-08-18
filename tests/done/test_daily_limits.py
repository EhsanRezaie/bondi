# tests/test_daily_limits.py
import pytest
from httpx import AsyncClient

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
REWARDS_LIMITS_URL = "/api/v1/rewards/my-limits"
SWIPE_STATS_URL = "/api/v1/swipes/stats"

VALID_EMAIL = "daily@example.com"
VALID_EMAIL2 = "daily2@example.com"
VALID_PASSWORD = "strongpass123"
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
    email: str,
    complete_payload: dict,
    mock_verification_code
) -> dict:
    """Complete full registration flow - returns user data with tokens."""
    # Step 1: Init
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text
    
    # Step 2: Store verification code
    await mock_verification_code(email, VALID_CODE)
    
    # Step 3: Verify
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email,
        "code": VALID_CODE,
        "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    
    # Step 4: Complete profile
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
    email: str,
    complete_payload: dict,
    mock_verification_code
) -> tuple[dict, str]:
    """Register a user and return headers with user_id."""
    result = await register_user_full(client, email, complete_payload, mock_verification_code)
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