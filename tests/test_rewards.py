
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"

REWARDS_LIMITS_URL = "/api/v1/rewards/my-limits"
REWARDS_AD_URL = "/api/v1/rewards/ad-watched"

VALID_PHONE = _phone("reward_user@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Reward User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, mock_verification_code=None, phone: str = VALID_PHONE) -> dict:
    """Helper: complete full registration via phone OTP flow."""
    if mock_verification_code:
        await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE, headers=headers)
    assert res.status_code == 200, res.text

    return res.json()


class TestRewards:
    """Test reward system (ad watching, daily limits)"""

    async def test_premium_user_from_welcome_bonus(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REWARDS_LIMITS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert body["is_premium"] is True
        assert body["likes_remaining_today"] == -1
        assert body["chats_remaining_today"] == -1
        assert body["premium_expires_at"] is not None
        assert body["max_ads_per_day"] == 2

    async def test_free_user_limits_from_config(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        pass

    async def test_ad_reward_success(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(REWARDS_AD_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert body["success"] is True
        assert body["likes_added"] == 5
        assert body["chats_added"] == 3
        assert body["ads_watched_today"] == 1
        assert body["max_ads_per_day"] == 2
        assert "gained" in body["message"]

    async def test_ad_reward_max_limit_reached(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res1 = await client.post(REWARDS_AD_URL, headers=headers)
        assert res1.status_code == 200

        res2 = await client.post(REWARDS_AD_URL, headers=headers)
        assert res2.status_code == 200

        res3 = await client.post(REWARDS_AD_URL, headers=headers)
        assert res3.status_code == 429
        body = res3.json()
        assert "Daily ad limit reached" in body["detail"]

    async def test_ad_reward_requires_auth(self, client: AsyncClient):
        res = await client.post(REWARDS_AD_URL)
        assert res.status_code == 401

    async def test_get_limits_requires_auth(self, client: AsyncClient):
        res = await client.get(REWARDS_LIMITS_URL)
        assert res.status_code == 401

    async def test_ad_reward_returns_correct_limits_structure(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REWARDS_LIMITS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        expected_fields = [
            "is_premium", "premium_expires_at", "likes_used_today",
            "likes_remaining_today", "chats_used_today", "chats_remaining_today",
            "ads_watched_today", "max_ads_per_day", "daily_likes_limit",
            "daily_chats_limit", "ad_reward_likes_bonus", "ad_reward_chats_bonus"
        ]
        for field in expected_fields:
            assert field in body

    async def test_limits_response_shape_premium(self, client: AsyncClient, mock_verification_code):
        """DailyLimitsResponse should show premium state with unlimited (-1)."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REWARDS_LIMITS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert body["is_premium"] is True
        assert isinstance(body["premium_expires_at"], str)
        assert isinstance(body["likes_used_today"], int)
        assert body["likes_remaining_today"] == -1
        assert isinstance(body["chats_used_today"], int)
        assert body["chats_remaining_today"] == -1
        assert isinstance(body["ads_watched_today"], int)
        assert isinstance(body["max_ads_per_day"], int)
        assert body["daily_likes_limit"] == -1
        assert body["daily_chats_limit"] == -1
        assert isinstance(body["ad_reward_likes_bonus"], int)
        assert isinstance(body["ad_reward_chats_bonus"], int)

    async def test_ad_reward_response_shape(self, client: AsyncClient, mock_verification_code):
        """AdRewardResponse should contain all schema fields."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(REWARDS_AD_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert body["success"] is True
        assert isinstance(body["likes_added"], int)
        assert body["likes_added"] == 5
        assert isinstance(body["chats_added"], int)
        assert body["chats_added"] == 3
        assert isinstance(body["ads_watched_today"], int)
        assert body["ads_watched_today"] == 1
        assert isinstance(body["max_ads_per_day"], int)
        assert body["max_ads_per_day"] == 2
        assert isinstance(body["message"], str)
