
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SUBSCRIPTION_PLANS_URL = "/api/v1/subscriptions/plans"
SUBSCRIPTION_PURCHASE_URL = "/api/v1/subscriptions/purchase"
SUBSCRIPTION_MY_URL = "/api/v1/subscriptions/my"
SUBSCRIPTION_CANCEL_URL = "/api/v1/subscriptions/cancel"
SUBSCRIPTION_VERIFY_URL = "/api/v1/subscriptions/verify"

VALID_PHONE = _phone("sub_user@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Sub User",
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
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=COMPLETE_PROFILE,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    return res.json()


class TestSubscriptions:
    """Test subscription system (plans, purchase mock, cancellation)"""

    async def test_get_plans_returns_correct_structure(self, client: AsyncClient):
        """Plans endpoint should return list of plans with prices."""
        res = await client.get(SUBSCRIPTION_PLANS_URL)
        assert res.status_code == 200
        body = res.json()

        assert "plans" in body
        assert len(body["plans"]) == 3

        plan_ids = [p["id"] for p in body["plans"]]
        assert "monthly" in plan_ids
        assert "quarterly" in plan_ids
        assert "yearly" in plan_ids

        # Check monthly plan structure
        monthly = body["plans"][0]
        expected_fields = ["id", "name", "days", "price_rials", "price_usd", "discount_percent"]
        for field in expected_fields:
            assert field in monthly

        assert monthly["days"] == 30
        assert monthly["price_rials"] > 0

    async def test_purchase_requires_auth(self, client: AsyncClient):
        """Cannot purchase subscription without authentication."""
        res = await client.post(SUBSCRIPTION_PURCHASE_URL, json={"plan_id": "monthly"})
        assert res.status_code == 401

    async def test_purchase_returns_redirect_url(self, client: AsyncClient, mock_verification_code):
        """Purchase should return mock ZarinPal redirect URL."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            SUBSCRIPTION_PURCHASE_URL,
            json={"plan_id": "monthly"},
            headers=headers
        )
        assert res.status_code == 200
        body = res.json()

        assert "redirect_url" in body
        assert "authority" in body
        assert "sandbox.zarinpal.com" in body["redirect_url"]
        assert len(body["authority"]) == 36

    async def test_purchase_invalid_plan(self, client: AsyncClient, mock_verification_code):
        """Purchase with invalid plan ID should return 400."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            SUBSCRIPTION_PURCHASE_URL,
            json={"plan_id": "invalid_plan"},
            headers=headers
        )
        assert res.status_code == 400
        assert "Invalid plan" in res.json()["detail"]

    async def test_get_my_subscription_after_welcome_bonus(self, client: AsyncClient, mock_verification_code):
        """After registration, user should have active welcome bonus subscription."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(SUBSCRIPTION_MY_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()

        assert body["is_premium"] is True
        assert body["plan"] == "welcome_bonus"
        assert body["source"] == "welcome_bonus"
        assert body["status"] == "active"
        assert body["started_at"] is not None
        assert body["expires_at"] is not None

    async def test_get_my_subscription_requires_auth(self, client: AsyncClient):
        """Cannot get subscription without authentication."""
        res = await client.get(SUBSCRIPTION_MY_URL)
        assert res.status_code == 401

    async def test_cancel_subscription(self, client: AsyncClient, mock_verification_code):
        """Cancelling subscription should change status to cancelled."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        # Cancel
        res = await client.post(SUBSCRIPTION_CANCEL_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert "cancelled" in body["message"]

        # Check subscription status
        my_res = await client.get(SUBSCRIPTION_MY_URL, headers=headers)
        my_body = my_res.json()
        assert my_body["status"] == "cancelled"

    async def test_cancel_subscription_no_active(self, client: AsyncClient, mock_verification_code):
        """Cancelling when no active subscription should return 404."""
        data = await register_user(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        # Cancel once
        await client.post(SUBSCRIPTION_CANCEL_URL, headers=headers)

        # Cancel again - should fail
        res = await client.post(SUBSCRIPTION_CANCEL_URL, headers=headers)
        assert res.status_code == 404
        assert "No active subscription found" in res.json()["detail"]

    async def test_cancel_requires_auth(self, client: AsyncClient):
        """Cannot cancel subscription without authentication."""
        res = await client.post(SUBSCRIPTION_CANCEL_URL)
        assert res.status_code == 401

    async def test_verify_payment_mock_success(self, client: AsyncClient, mock_verification_code):
        """Mock verify endpoint should return success."""
        data = await register_user(client, mock_verification_code)

        # Call verify with mock params
        res = await client.get(
            SUBSCRIPTION_VERIFY_URL,
            params={
                "authority": "MOCK_AUTHORITY_123",
                "status": "OK",
                "user_id": data["user"]["id"],
                "plan": "monthly"
            }
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert "verified" in body["message"]
        assert body["ref_id"] is not None

    async def test_verify_payment_failed_status(self, client: AsyncClient):
        """Verify with status NOK should return failure."""
        res = await client.get(
            SUBSCRIPTION_VERIFY_URL,
            params={"authority": "MOCK", "status": "NOK"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "canceled" in body["message"]