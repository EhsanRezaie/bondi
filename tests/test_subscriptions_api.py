# tests/test_subscriptions_api.py
"""Coverage tests for app/api/v1/endpoints/subscriptions.py (the mounted
subscription router with the mock ZarinPal gateway flow).

Covers plans (with cache), purchase (valid/invalid plan), verify flow
(unknown authority, wrong owner, canceled payment, single-use consumption,
success → premium + Subscription row), status lookup and cancel lifecycle.
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.subscription import Subscription
from tests.test_chats import PAYLOAD_MALE, _phone, register_and_get_headers

BASE = "/api/v1/subscriptions"


async def _register(client: AsyncClient, mock_verification_code, key: str) -> tuple[dict, str]:
    headers, user_id = await register_and_get_headers(
        client, _phone(f"{key}@example.com"), PAYLOAD_MALE, mock_verification_code
    )
    return headers, user_id


class TestPlans:
    async def test_get_plans(self, client: AsyncClient):
        res = await client.get(f"{BASE}/plans")
        assert res.status_code == 200, res.text
        assert res.headers["Cache-Control"] == "public, max-age=3600"
        plans = {p["id"]: p for p in res.json()["plans"]}
        assert set(plans) == {"monthly", "quarterly", "yearly"}
        assert plans["quarterly"]["discount_percent"] > 0
        assert plans["yearly"]["discount_percent"] > plans["quarterly"]["discount_percent"]


class TestPurchase:
    async def test_purchase_records_pending_and_returns_redirect(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        headers, user_id = await _register(client, mock_verification_code, "sub_p")
        res = await client.post(f"{BASE}/purchase", json={"plan_id": "monthly"}, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["authority"]
        assert body["redirect_url"].endswith(body["authority"])

        from app.models.subscription import PendingPurchase

        pending = (
            await db_session.execute(
                select(PendingPurchase).where(PendingPurchase.authority == body["authority"])
            )
        ).scalar_one()
        assert str(pending.user_id) == user_id
        assert pending.status == "pending"
        assert pending.plan == "monthly"
        assert pending.amount_rials == 50000

    async def test_purchase_invalid_plan_400(self, client: AsyncClient, mock_verification_code):
        headers, _ = await _register(client, mock_verification_code, "sub_pi")
        res = await client.post(f"{BASE}/purchase", json={"plan_id": "weekly"}, headers=headers)
        assert res.status_code == 400
        assert res.json()["detail"] == "Invalid plan"


class TestVerify:
    async def _purchase(self, client: AsyncClient, headers: dict, plan: str = "monthly") -> str:
        res = await client.post(f"{BASE}/purchase", json={"plan_id": plan}, headers=headers)
        assert res.status_code == 200, res.text
        return res.json()["authority"]

    async def test_verify_canceled_payment(self, client: AsyncClient, mock_verification_code):
        headers, _ = await _register(client, mock_verification_code, "sub_vc")
        authority = await self._purchase(client, headers)
        res = await client.get(
            f"{BASE}/verify",
            params={"authority": authority, "status": "NOK"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is False
        assert "canceled" in body["message"]

    async def test_verify_unknown_authority(self, client: AsyncClient, mock_verification_code):
        headers, _ = await _register(client, mock_verification_code, "sub_vu")
        res = await client.get(
            f"{BASE}/verify",
            params={"authority": "no-such-authority", "status": "OK"},
            headers=headers,
        )
        assert res.status_code == 200
        assert "Unknown payment reference" in res.json()["message"]

    async def test_verify_wrong_owner_403(self, client: AsyncClient, mock_verification_code):
        owner_headers, _ = await _register(client, mock_verification_code, "sub_vo")
        other_headers, _ = await _register(client, mock_verification_code, "sub_vo2")
        authority = await self._purchase(client, owner_headers)

        res = await client.get(
            f"{BASE}/verify",
            params={"authority": authority, "status": "OK"},
            headers=other_headers,
        )
        assert res.status_code == 403
        assert "does not belong" in res.json()["detail"]

    async def test_verify_single_use(self, client: AsyncClient, mock_verification_code):
        headers, _ = await _register(client, mock_verification_code, "sub_v1x")
        authority = await self._purchase(client, headers)

        first = await client.get(
            f"{BASE}/verify",
            params={"authority": authority, "status": "OK"},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["success"] is True
        assert first.json()["ref_id"].startswith("MOCK_REF_")

        second = await client.get(
            f"{BASE}/verify",
            params={"authority": authority, "status": "OK"},
            headers=headers,
        )
        assert second.status_code == 200
        assert "already processed" in second.json()["message"]


class TestStatusAndCancel:
    async def test_my_fresh_user_shows_welcome_bonus(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        headers, user_id = await _register(client, mock_verification_code, "sub_m0")
        # Onboarding grants a welcome-bonus premium: a Subscription row with
        # source="welcome_bonus" exists for every fresh user.
        row = (
            await db_session.execute(select(Subscription).where(Subscription.user_id == user_id))
        ).scalar_one()
        assert row.source == "welcome_bonus"
        assert row.status == "active"

        res = await client.get(f"{BASE}/my", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["is_premium"] is True
        assert body["plan"] == "welcome_bonus"
        assert body["status"] == "active"

    async def test_cancel_without_active_subscription_404(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        headers, _ = await _register(client, mock_verification_code, "sub_c0")
        # First cancel consumes the welcome-bonus subscription.
        first = await client.post(f"{BASE}/cancel", headers=headers)
        assert first.status_code == 200, first.text

        res = await client.post(f"{BASE}/cancel", headers=headers)
        assert res.status_code == 404
        assert res.json()["detail"] == "No active subscription found"

    async def test_verify_then_status_then_cancel(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        headers, user_id = await _register(client, mock_verification_code, "sub_c1")
        res = await client.post(f"{BASE}/purchase", json={"plan_id": "quarterly"}, headers=headers)
        authority = res.json()["authority"]

        res = await client.get(
            f"{BASE}/verify",
            params={"authority": authority, "status": "OK"},
            headers=headers,
        )
        assert res.status_code == 200

        res = await client.get(f"{BASE}/my", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["is_premium"] is True
        assert body["plan"] == "quarterly"
        assert body["status"] == "active"
        assert body["source"] == "purchase"
        assert body["expires_at"] is not None

        res = await client.post(f"{BASE}/cancel", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["success"] is True

        row = (
            (
                await db_session.execute(
                    select(Subscription)
                    .where(Subscription.user_id == user_id)
                    .order_by(Subscription.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        assert row.status == "cancelled"

        # Status endpoint reflects the cancelled record (still premium_until).
        res = await client.get(f"{BASE}/my", headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
