
import pytest
from httpx import AsyncClient
from app.core.config import settings
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


ADMIN_DASHBOARD_URL = "/api/v1/admin/dashboard"
ADMIN_KEY = settings.ADMIN_SECRET_KEY

VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, phone: str, mock_verification_code) -> dict:
    """Helper: complete full registration via phone OTP flow."""
    await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE, headers=headers)
    assert res.status_code == 200, res.text

    return res.json()


class TestAdminDashboard:
    """Test admin dashboard statistics"""

    async def test_dashboard_overview_success(self, client: AsyncClient, mock_verification_code):
        """Admin should get dashboard overview stats"""
        for i in range(3):
            await register_user(client, _phone(f"dashboard_{i}@example.com"), mock_verification_code)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(ADMIN_DASHBOARD_URL, headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert "total_users" in body
        assert "active_today" in body
        assert "new_users_today" in body
        assert "new_users_this_week" in body
        assert "premium_users" in body
        assert "premium_percentage" in body
        assert "total_swipes_today" in body
        assert "total_matches_today" in body
        assert "total_messages_today" in body
        assert "pending_photos" in body
        assert "pending_reports" in body
        assert "open_tickets" in body

        assert body["total_users"] >= 3

    async def test_user_growth_stats(self, client: AsyncClient):
        """Admin should get user growth chart data"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_DASHBOARD_URL}/stats/users",
            params={"days": 7},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()

        assert "labels" in body
        assert "new_users" in body
        assert "active_users" in body
        assert len(body["labels"]) == 7
        assert len(body["new_users"]) == 7
        assert len(body["active_users"]) == 7

    async def test_activity_stats(self, client: AsyncClient):
        """Admin should get activity chart data"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_DASHBOARD_URL}/stats/activity",
            params={"days": 7},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()

        assert "labels" in body
        assert "swipes" in body
        assert "matches" in body
        assert "messages" in body
        assert len(body["labels"]) == 7

    async def test_report_stats(self, client: AsyncClient):
        """Admin should get report statistics"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_DASHBOARD_URL}/stats/reports",
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()

        assert "pending" in body
        assert "reviewed" in body
        assert "action_taken" in body

    async def test_ticket_stats(self, client: AsyncClient):
        """Admin should get ticket statistics"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_DASHBOARD_URL}/stats/tickets",
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()

        assert "open" in body
        assert "in_progress" in body
        assert "closed" in body

    async def test_dashboard_requires_admin_key(self, client: AsyncClient):
        """Should return 403 without admin key"""
        res = await client.get(ADMIN_DASHBOARD_URL)
        assert res.status_code == 403
