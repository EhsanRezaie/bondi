"""Chat lifecycle tests for behavior not exercised by test_chats.py:
blocked-pair create, pair open-cap 429, accept authorization matrix,
chat detail authorization, delete/leave lifecycle, daily-chat-limit 429.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import update

from app.models.user_profile import UserProfile
from tests.test_chats import (
    CHATS_URL,
    PAYLOAD_FEMALE,
    PAYLOAD_MALE,
    _phone,
    register_and_get_headers,
)

MESSAGES_URL = "/api/v1/messages"
BLOCKS_URL = "/api/v1/blocks"


async def _make_pair(
    client: AsyncClient, mock_verification_code, key: str = "cov"
) -> tuple[dict, str, dict, str]:
    male_headers, male_id = await register_and_get_headers(
        client, _phone(f"{key}_male@example.com"), PAYLOAD_MALE, mock_verification_code
    )
    female_headers, female_id = await register_and_get_headers(
        client, _phone(f"{key}_female@example.com"), PAYLOAD_FEMALE, mock_verification_code
    )
    return male_headers, male_id, female_headers, female_id


async def _like(client: AsyncClient, headers: dict, target_id: str) -> None:
    res = await client.post(
        "/api/v1/swipes", json={"user_id": target_id, "direction": "like"}, headers=headers
    )
    assert res.status_code == 200, res.text


async def _create_chat(
    client: AsyncClient, headers: dict, target_id: str, content: str = "hi"
):
    return await client.post(
        CHATS_URL,
        json={"user_id": target_id, "content": content},
        headers=headers,
    )


class TestCreateChatGuards:
    async def test_create_chat_with_nonexistent_user_404(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _, _, _ = await _make_pair(client, mock_verification_code, "cnf")
        res = await _create_chat(client, male_headers, str(uuid.uuid4()))
        assert res.status_code == 404

    async def test_create_chat_blocked_pair_403(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id, female_headers, female_id = await _make_pair(
            client, mock_verification_code, "cblk"
        )
        res = await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        assert res.status_code == 204, res.text

        res = await _create_chat(client, male_headers, female_id)
        assert res.status_code == 403
        assert "cannot start" in res.json()["detail"]

        # Reverse direction is blocked too — block excludes the pair both ways.
        res = await _create_chat(client, female_headers, male_id)
        assert res.status_code == 403

    async def test_reopen_after_delete_returns_same_ended_chat(
        self, client: AsyncClient, mock_verification_code
    ):
        """Current behavior: delete hides the chat from the deleter's list but
        keeps the row, so re-opening the pair returns the same (ended) chat
        rather than creating a new one."""
        male_headers, _, _, female_id = await _make_pair(client, mock_verification_code, "ccap")
        res = await _create_chat(client, male_headers, female_id, "hello")
        assert res.status_code == 200
        chat_id = res.json()["chat_id"]
        assert res.json()["is_new"] is True

        res = await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)
        assert res.status_code == 204

        res = await _create_chat(client, male_headers, female_id, "again")
        assert res.status_code == 200
        assert res.json()["is_new"] is False
        assert str(res.json()["chat_id"]) == chat_id


class TestAcceptChat:
    async def _pending_chat(self, client, mock_verification_code, key: str):
        male_headers, male_id, female_headers, female_id = await _make_pair(
            client, mock_verification_code, key
        )
        await _like(client, male_headers, female_id)
        res = await _create_chat(client, male_headers, female_id)
        assert res.status_code == 200
        return male_headers, male_id, female_headers, female_id, res.json()

    async def test_initiator_cannot_accept_own_chat(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _, _, _, chat = await self._pending_chat(
            client, mock_verification_code, "acc1"
        )
        res = await client.post(f"{CHATS_URL}/{chat['chat_id']}/accept", headers=male_headers)
        assert res.status_code == 403
        assert "Only the recipient" in res.json()["detail"]

    async def test_non_member_cannot_accept(
        self, client: AsyncClient, mock_verification_code
    ):
        _, _, _, _, chat = await self._pending_chat(client, mock_verification_code, "acc2")
        third_headers, _ = await register_and_get_headers(
            client, _phone("acc2_third@example.com"), PAYLOAD_FEMALE, mock_verification_code
        )
        res = await client.post(f"{CHATS_URL}/{chat['chat_id']}/accept", headers=third_headers)
        assert res.status_code == 404

    async def test_accept_is_idempotent(self, client: AsyncClient, mock_verification_code):
        _, _, female_headers, _, chat = await self._pending_chat(
            client, mock_verification_code, "acc3"
        )
        res1 = await client.post(f"{CHATS_URL}/{chat['chat_id']}/accept", headers=female_headers)
        assert res1.status_code == 200
        assert res1.json()["status"] == "accepted"

        res2 = await client.post(f"{CHATS_URL}/{chat['chat_id']}/accept", headers=female_headers)
        assert res2.status_code == 200
        assert "already accepted" in res2.json()["message"]


class TestChatDetailAndDelete:
    async def _accepted_chat(self, client, mock_verification_code, key: str):
        male_headers, male_id, female_headers, female_id = await _make_pair(
            client, mock_verification_code, key
        )
        await _like(client, male_headers, female_id)
        await _like(client, female_headers, male_id)
        res = await _create_chat(client, male_headers, female_id)
        assert res.status_code == 200
        return male_headers, female_headers, res.json()

    async def test_detail_member_and_non_member(
        self, client: AsyncClient, mock_verification_code
    ):
        _male_headers, female_headers, chat = await self._accepted_chat(
            client, mock_verification_code, "det"
        )
        res = await client.get(f"{CHATS_URL}/{chat['chat_id']}", headers=female_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"
        assert res.json()["user"]["name"] == PAYLOAD_MALE["name"]

        third_headers, _ = await register_and_get_headers(
            client, _phone("det_third@example.com"), PAYLOAD_FEMALE, mock_verification_code
        )
        res = await client.get(f"{CHATS_URL}/{chat['chat_id']}", headers=third_headers)
        assert res.status_code == 404

    async def test_delete_hides_for_deleter_and_ends_for_other(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, female_headers, chat = await self._accepted_chat(
            client, mock_verification_code, "del"
        )
        res = await client.delete(f"{CHATS_URL}/{chat['chat_id']}", headers=male_headers)
        assert res.status_code == 204

        # Deleter's detail → 403 "You have deleted this chat".
        res = await client.get(f"{CHATS_URL}/{chat['chat_id']}", headers=male_headers)
        assert res.status_code == 403
        assert "deleted this chat" in res.json()["detail"]

        # Other participant can still view (ended flag, status unchanged) —
        # and their delete also works.
        res = await client.get(f"{CHATS_URL}/{chat['chat_id']}", headers=female_headers)
        assert res.status_code == 200
        assert res.json()["is_ended"] is True

        res = await client.delete(f"{CHATS_URL}/{chat['chat_id']}", headers=female_headers)
        assert res.status_code == 204

    async def test_delete_nonexistent_chat_404(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _, _, _ = await _make_pair(client, mock_verification_code, "del404")
        res = await client.delete(f"{CHATS_URL}/{uuid.uuid4()}", headers=male_headers)
        assert res.status_code == 404


class TestDailyChatLimit:
    async def test_free_user_daily_chat_cap_429(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id, _female_headers, _female_id = await _make_pair(
            client, mock_verification_code, "dlm"
        )
        # Welcome bonus makes fresh users premium (unlimited chats) — demote
        # the sender to free to exercise the cap (is_premium is a property
        # over premium_until).
        await db_session.execute(
            update(UserProfile)
            .where(UserProfile.user_id == male_id)
            .values(premium_until=None)
        )
        await db_session.commit()

        targets = []
        for i in range(11):
            _, fid = await register_and_get_headers(
                client,
                _phone(f"dlm_t{i}@example.com"),
                PAYLOAD_FEMALE,
                mock_verification_code,
            )
            targets.append(fid)

        for i, fid in enumerate(targets[:10]):
            res = await _create_chat(client, male_headers, fid, f"chat {i}")
            assert res.status_code == 200, res.text

        res = await _create_chat(client, male_headers, targets[10], "one too many")
        assert res.status_code == 429
        assert "Daily new chat limit reached" in res.json()["detail"]
