
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
REFERRAL_CODE_URL = "/api/v1/referrals/my-code"
REFERRAL_CLAIM_URL = "/api/v1/referrals/claim"
REFERRAL_STATS_URL = "/api/v1/referrals/stats"

VALID_PHONE = _phone("referrer@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Referrer",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, phone: str = VALID_PHONE, mock_verification_code=None) -> dict:
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


class TestReferrals:
    """Test referral system"""

    async def test_get_referral_code(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REFERRAL_CODE_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert "referral_code" in body
        assert len(body["referral_code"]) == 8
        assert "share_text" in body
        assert "Join me on DatingApp" in body["share_text"]

    async def test_referral_code_persists(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res1 = await client.get(REFERRAL_CODE_URL, headers=headers)
        code1 = res1.json()["referral_code"]

        res2 = await client.get(REFERRAL_CODE_URL, headers=headers)
        code2 = res2.json()["referral_code"]

        assert code1 == code2

    async def test_claim_referral_success(self, client: AsyncClient, mock_verification_code):
        inviter_data = await register_user(client, mock_verification_code=mock_verification_code)
        inviter_headers = {"Authorization": f"Bearer {inviter_data['access_token']}"}

        code_res = await client.get(REFERRAL_CODE_URL, headers=inviter_headers)
        referral_code = code_res.json()["referral_code"]

        invited_data = await register_user(client, _phone("invited@example.com"), mock_verification_code)
        invited_headers = {"Authorization": f"Bearer {invited_data['access_token']}"}

        claim_res = await client.post(
            REFERRAL_CLAIM_URL,
            json={"referral_code": referral_code},
            headers=invited_headers
        )
        assert claim_res.status_code == 200

        inviter_stats = await client.get(REFERRAL_STATS_URL, headers=inviter_headers)
        assert inviter_stats.status_code == 200
        stats = inviter_stats.json()
        assert stats["successful_referrals"] == 1
        assert stats["total_premium_days_earned"] == 3

    async def test_claim_referral_requires_code(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(REFERRAL_CLAIM_URL, json={}, headers=headers)
        assert res.status_code == 400
        assert "Referral code required" in res.json()["detail"]

    async def test_claim_invalid_referral_code(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            REFERRAL_CLAIM_URL,
            json={"referral_code": "INVALID123"},
            headers=headers
        )
        assert res.status_code == 404
        assert "Invalid referral code" in res.json()["detail"]

    async def test_cannot_use_own_referral_code(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        code_res = await client.get(REFERRAL_CODE_URL, headers=headers)
        own_code = code_res.json()["referral_code"]

        res = await client.post(
            REFERRAL_CLAIM_URL,
            json={"referral_code": own_code},
            headers=headers
        )
        assert res.status_code == 400
        assert "Cannot use your own referral code" in res.json()["detail"]

    async def test_cannot_claim_referral_twice(self, client: AsyncClient, mock_verification_code):
        inviter_data = await register_user(client, mock_verification_code=mock_verification_code)
        inviter_headers = {"Authorization": f"Bearer {inviter_data['access_token']}"}
        code_res = await client.get(REFERRAL_CODE_URL, headers=inviter_headers)
        referral_code = code_res.json()["referral_code"]

        invited_data = await register_user(client, _phone("invited2@example.com"), mock_verification_code)
        invited_headers = {"Authorization": f"Bearer {invited_data['access_token']}"}

        claim_res = await client.post(
            REFERRAL_CLAIM_URL,
            json={"referral_code": referral_code},
            headers=invited_headers
        )
        assert claim_res.status_code == 200

        res = await client.post(
            REFERRAL_CLAIM_URL,
            json={"referral_code": "ANOTHER123"},
            headers=invited_headers
        )
        assert res.status_code == 400
        assert "Referral already claimed" in res.json()["detail"]

    async def test_referral_stats_structure(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REFERRAL_STATS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        expected_fields = [
            "referral_code", "successful_referrals", "total_premium_days_earned",
            "inviter_reward_days", "invited_reward_days", "is_premium", "premium_until"
        ]
        for field in expected_fields:
            assert field in body

    async def test_referral_requires_auth(self, client: AsyncClient):
        res1 = await client.get(REFERRAL_CODE_URL)
        assert res1.status_code == 401

        res2 = await client.post(REFERRAL_CLAIM_URL, json={"referral_code": "TEST123"})
        assert res2.status_code == 401

        res3 = await client.get(REFERRAL_STATS_URL)
        assert res3.status_code == 401

    async def test_claim_referral_response_shape(self, client: AsyncClient, mock_verification_code):
        """ClaimReferralResponse should match schema."""
        inviter = await register_user(client, mock_verification_code=mock_verification_code)
        inviter_headers = {"Authorization": f"Bearer {inviter['access_token']}"}
        code_res = await client.get(REFERRAL_CODE_URL, headers=inviter_headers)
        code = code_res.json()["referral_code"]

        invited = await register_user(client, _phone("claim_shape@example.com"), mock_verification_code)
        invited_headers = {"Authorization": f"Bearer {invited['access_token']}"}

        res = await client.post(REFERRAL_CLAIM_URL, json={"referral_code": code}, headers=invited_headers)
        assert res.status_code == 200
        body = res.json()

        assert body["success"] is True
        assert isinstance(body["message"], str)
        assert "free premium days" in body["message"]
        assert isinstance(body["your_referral_code"], str)
        assert len(body["your_referral_code"]) == 8

    async def test_referral_code_response_shape(self, client: AsyncClient, mock_verification_code):
        """ReferralCodeResponse should match schema."""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REFERRAL_CODE_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert isinstance(body["referral_code"], str)
        assert len(body["referral_code"]) == 8
        assert isinstance(body["share_text"], str)
        assert body["referral_code"] in body["share_text"]

    async def test_referral_stats_response_shape(self, client: AsyncClient, mock_verification_code):
        """ReferralStatsResponse should match schema."""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(REFERRAL_STATS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert isinstance(body["referral_code"], str)
        assert len(body["referral_code"]) == 8
        assert isinstance(body["successful_referrals"], int)
        assert isinstance(body["total_premium_days_earned"], int)
        assert isinstance(body["inviter_reward_days"], int)
        assert isinstance(body["invited_reward_days"], int)
        assert isinstance(body["is_premium"], bool)
        assert body["premium_until"] is not None  # welcome bonus
