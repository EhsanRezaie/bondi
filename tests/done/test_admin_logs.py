# tests/done/test_admin_logs.py
import uuid
import pytest
from httpx import AsyncClient
from datetime import datetime, timezone

from app.models.admin_log import AdminLog
from app.core.config import settings

ADMIN_LOGS_URL = "/api/v1/admin/logs"


def admin_headers() -> dict:
    return {"X-Admin-Key": settings.ADMIN_SECRET_KEY}


class TestAdminLogs:

    async def test_list_logs_requires_auth(self, client: AsyncClient):
        res = await client.get(ADMIN_LOGS_URL)
        assert res.status_code == 403

    async def test_list_logs_empty(self, client: AsyncClient):
        res = await client.get(ADMIN_LOGS_URL, headers=admin_headers())
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["logs"] == []

    async def test_list_logs_with_jwt(self, client: AsyncClient):
        from app.core.security import create_admin_token
        token = create_admin_token("some-admin")
        res = await client.get(
            ADMIN_LOGS_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    async def test_list_logs_filters_and_pagination(
        self, client: AsyncClient, db_session
    ):
        now = datetime.now(timezone.utc)
        rows = [
            AdminLog(
                id=uuid.uuid4(),
                admin_id="boss",
                action="user_update",
                target_type="user",
                target_id=uuid.uuid4(),
                ip_address="127.0.0.1",
                created_at=now,
            ),
            AdminLog(
                id=uuid.uuid4(),
                admin_id="boss",
                action="ticket_update",
                target_type="ticket",
                target_id=uuid.uuid4(),
                ip_address="127.0.0.1",
                created_at=now,
            ),
            AdminLog(
                id=uuid.uuid4(),
                admin_id="helper",
                action="user_update",
                target_type="user",
                target_id=uuid.uuid4(),
                ip_address="10.0.0.1",
                created_at=now,
            ),
        ]
        db_session.add_all(rows)
        await db_session.commit()

        res = await client.get(ADMIN_LOGS_URL, headers=admin_headers())
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["logs"]) == 3

        res = await client.get(
            ADMIN_LOGS_URL,
            params={"action": "user_update", "page_size": 1},
            headers=admin_headers(),
        )
        data = res.json()
        assert data["total"] == 2
        assert len(data["logs"]) == 1

        res = await client.get(
            ADMIN_LOGS_URL,
            params={"admin_id": "boss", "action": "ticket_update"},
            headers=admin_headers(),
        )
        data = res.json()
        assert data["total"] == 1
        assert data["logs"][0]["action"] == "ticket_update"
        assert data["logs"][0]["admin_id"] == "boss"