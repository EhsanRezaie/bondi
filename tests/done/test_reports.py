
import pytest
from httpx import AsyncClient
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
REPORTS_URL = "/api/v1/reports"

VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Test User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, phone: str, mock_verification_code=None) -> dict:
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


class TestReports:
    """Test user reporting system"""

    async def test_report_user_success(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target@example.com"), mock_verification_code)

        res = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "Inappropriate behavior - sending unwanted messages"},
            headers=reporter_headers
        )
        assert res.status_code == 201
        body = res.json()
        assert body["reported_user_id"] == target_data["user"]["id"]
        assert body["reason"] == "Inappropriate behavior - sending unwanted messages"
        assert body["status"] == "pending"
        assert "created_at" in body

    async def test_report_user_minimal_reason(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter2@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target2@example.com"), mock_verification_code)

        res = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "Spammy"},
            headers=reporter_headers
        )
        assert res.status_code == 201

    async def test_report_user_reason_too_short(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter3@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target3@example.com"), mock_verification_code)

        res = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "Bad"},
            headers=reporter_headers
        )
        assert res.status_code == 422

    async def test_report_user_reason_too_long(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter4@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target4@example.com"), mock_verification_code)

        long_reason = "a" * 501
        res = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": long_reason},
            headers=reporter_headers
        )
        assert res.status_code == 422

    async def test_cannot_report_self(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("self@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            f"{REPORTS_URL}/{data['user']['id']}",
            json={"reason": "Testing self report"},
            headers=headers
        )
        assert res.status_code == 400
        assert "Cannot report yourself" in res.json()["detail"]

    async def test_cannot_report_nonexistent_user(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("nonexist@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            f"{REPORTS_URL}/00000000-0000-0000-0000-000000000001",
            json={"reason": "This user doesn't exist"},
            headers=headers
        )
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]

    async def test_cannot_report_same_user_twice_in_24h(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter5@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target5@example.com"), mock_verification_code)

        res1 = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "First report"},
            headers=reporter_headers
        )
        assert res1.status_code == 201

        res2 = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "Second report"},
            headers=reporter_headers
        )
        assert res2.status_code == 400
        assert "already reported this user recently" in res2.json()["detail"]

    async def test_report_multiple_different_users_allowed(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter_multi@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        for i in range(3):
            target_data = await register_user(
                client, _phone(f"target_multi_{i}@example.com"), mock_verification_code
            )

            res = await client.post(
                f"{REPORTS_URL}/{target_data['user']['id']}",
                json={"reason": f"Report #{i}"},
                headers=reporter_headers
            )
            assert res.status_code == 201

    async def test_report_requires_auth(self, client: AsyncClient):
        res = await client.post(
            f"{REPORTS_URL}/00000000-0000-0000-0000-000000000001",
            json={"reason": "No auth"}
        )
        assert res.status_code == 401

    async def test_get_my_reports_empty(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("empty@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(f"{REPORTS_URL}/my", headers=headers)
        assert res.status_code == 200
        assert res.json() == []

    async def test_get_my_reports_with_data(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("myreports@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        reported_ids = []
        for i in range(3):
            target_data = await register_user(
                client, _phone(f"target_my_{i}@example.com"), mock_verification_code
            )
            reported_ids.append(target_data["user"]["id"])

            await client.post(
                f"{REPORTS_URL}/{target_data['user']['id']}",
                json={"reason": f"My report #{i}"},
                headers=reporter_headers
            )

        res = await client.get(f"{REPORTS_URL}/my", headers=reporter_headers)
        assert res.status_code == 200
        reports = res.json()
        assert len(reports) == 3

        report_user_ids = [r["reported_user_id"] for r in reports]
        for reported_id in reported_ids:
            assert str(reported_id) in report_user_ids

    async def test_get_my_reports_requires_auth(self, client: AsyncClient):
        res = await client.get(f"{REPORTS_URL}/my")
        assert res.status_code == 401

    async def test_report_user_who_deleted_account(self, client: AsyncClient, mock_verification_code):
        reporter_data = await register_user(client, _phone("reporter_del@example.com"), mock_verification_code)
        reporter_headers = {"Authorization": f"Bearer {reporter_data['access_token']}"}

        target_data = await register_user(client, _phone("target_delete@example.com"), mock_verification_code)
        target_headers = {"Authorization": f"Bearer {target_data['access_token']}"}

        await client.delete("/api/v1/users/me", headers=target_headers)

        res = await client.post(
            f"{REPORTS_URL}/{target_data['user']['id']}",
            json={"reason": "User was inappropriate before deletion"},
            headers=reporter_headers
        )
        assert res.status_code == 201
