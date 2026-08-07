# tests/done/test_chat_features.py
# Covers the phase-1 backend work end to end:
#   1A message edit, 1B message reports, 1C block chat-not-hidden, 1D delete/end chat.
import uuid
from sqlalchemy import select

from httpx import AsyncClient

from app.models.chat import Chat
from app.models.message import Message
from app.models.report import Report
from app.models.block import Block

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
CHATS_URL = "/api/v1/chats"
MESSAGES_URL = "/api/v1/messages"
REPORTS_URL = "/api/v1/reports"
BLOCKS_URL = "/api/v1/blocks"

VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

PAYLOAD_MALE = {
    "name": "Chat Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 180,
    "weight": 75,
}

PAYLOAD_FEMALE = {
    "name": "Chat Female",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 165,
    "weight": 60,
}


async def register_and_get_headers(client, email, complete_payload, mock_verification_code):
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text
    await mock_verification_code(email, VALID_CODE)
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email, "code": VALID_CODE, "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=complete_payload, headers=headers)
    assert res.status_code == 200, res.text
    result = res.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    return headers, result["user"]["id"]


async def make_match(client, male_headers, female_id, female_headers, male_id):
    await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
    await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)
    res = await client.post(
        CHATS_URL, json={"user_id": female_id, "content": "Hi!"}, headers=male_headers
    )
    assert res.status_code == 200, res.text
    return res.json()["chat_id"]


async def send_text(client, headers, chat_id, content):
    res = await client.post(
        f"{MESSAGES_URL}/{chat_id}/text", json={"content": content}, headers=headers
    )
    assert res.status_code == 200, res.text
    return res.json()


class TestMessageEdit:
    """1A - edit own text message, marked as edited."""

    async def test_edit_own_message(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, "edit_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "edit_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "original")

        res = await client.put(
            f"{MESSAGES_URL}/{msg['id']}", json={"content": "edited version"}, headers=male_headers
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["content"] == "edited version"
        assert body["is_edited"] is True
        assert body["edited_at"] is not None

    async def test_edit_without_auth_401(self, client, mock_verification_code):
        res = await client.put(
            f"{MESSAGES_URL}/{uuid.uuid4()}", json={"content": "x"}
        )
        assert res.status_code == 401

    async def test_non_owner_cannot_edit(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "edit2_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "edit2_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "hey")

        res = await client.put(
            f"{MESSAGES_URL}/{msg['id']}", json={"content": "hacked"}, headers=female_headers
        )
        assert res.status_code in (400, 403)

    async def test_edit_empty_or_whitespace_fails(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "edit3_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "edit3_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "hey")

        res = await client.put(
            f"{MESSAGES_URL}/{msg['id']}", json={"content": "   "}, headers=male_headers
        )
        assert res.status_code == 400

    async def test_non_participant_cannot_edit(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "edit4_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "edit4_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, _ = await register_and_get_headers(
            client, "edit4_third@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "secret")

        res = await client.put(
            f"{MESSAGES_URL}/{msg['id']}", json={"content": "stolen"}, headers=third_headers
        )
        assert res.status_code in (400, 403)


class TestMessageReport:
    """1B - report a message (new backend endpoint)."""

    admin_message_url = "/api/v1/admin/messages"

    async def _make_msg(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "rep_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "rep_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "bad message content")
        return male_headers, female_headers, msg["id"]

    async def _make_msg_and_report(self, client, mock_verification_code):
        male_headers, female_headers, msg_id = await self._make_msg(client, mock_verification_code)
        res = await client.post(
            f"{REPORTS_URL}/message/{msg_id}",
            json={"reason": "inappropriate", "description": "offensive words"},
            headers=female_headers,
        )
        assert res.status_code == 201, res.text
        return female_headers, msg_id

    async def test_report_message(self, client, mock_verification_code, db_session, admin_headers):
        male_headers, female_headers, msg_id = await self._make_msg(client, mock_verification_code)
        res = await client.post(
            f"{REPORTS_URL}/message/{msg_id}",
            json={"reason": "inappropriate", "description": "offensive words"},
            headers=female_headers,
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["message_id"] == str(msg_id)
        assert body["is_message_report"] is True

        report = (await db_session.execute(
            select(Report).where(Report.message_id == uuid.UUID(msg_id))
        )).scalar_one()
        assert report.is_message_report is True
        assert report.description == "offensive words"

    async def test_report_message_not_participant(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "rep2_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "rep2_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        stranger_headers, _ = await register_and_get_headers(
            client, "rep2_stranger@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        msg = await send_text(client, male_headers, chat_id, "secret")

        res = await client.post(
            f"{REPORTS_URL}/message/{msg['id']}", json={"reason": "not mine"}, headers=stranger_headers
        )
        assert res.status_code == 403

    async def test_report_own_message_fails(self, client, mock_verification_code):
        male_headers, _, msg_id = await self._make_msg(client, mock_verification_code)
        res = await client.post(
            f"{REPORTS_URL}/message/{msg_id}", json={"reason": "myself"}, headers=male_headers
        )
        assert res.status_code == 400

    async def test_duplicate_report_24h_blocked(self, client, mock_verification_code):
        female_headers, msg_id = await self._make_msg_and_report(client, mock_verification_code)
        await client.post(
            f"{REPORTS_URL}/message/{msg_id}", json={"reason": "first"}, headers=female_headers
        )
        res = await client.post(
            f"{REPORTS_URL}/message/{msg_id}", json={"reason": "second"}, headers=female_headers
        )
        assert res.status_code == 400

    async def test_admin_view_reported_message(self, client, mock_verification_code, db_session, admin_headers):
        male_headers, female_headers, msg_id = await self._make_msg(client, mock_verification_code)
        await client.post(
            f"{REPORTS_URL}/message/{msg_id}", json={"reason": "spam report"}, headers=female_headers
        )
        report = (await db_session.execute(
            select(Report).where(Report.message_id == uuid.UUID(msg_id))
        )).scalar_one()
        res = await client.get(
            f"/api/v1/admin/messages/reports/{report.id}/message", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["message_id"] == str(msg_id)


class TestBlockChatNotHidden:
    """1C - Blocking marks the chat as over, but it stays visible."""

    async def test_send_in_blocked_chat_forbidden(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "b_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "b_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)

        res = await client.post(f"{MESSAGES_URL}/{chat_id}/text", json={"content": "hello"}, headers=male_headers)
        assert res.status_code == 403

    async def test_detail_flags_ended_chat(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "b2_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "b2_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)

        detail = await client.get(f"{CHATS_URL}/{chat_id}", headers=male_headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["is_blocked"] is True
        assert detail.json()["is_ended"] is True

    async def test_block_broadcasts_blocked_event(
        self, client, mock_verification_code
    ):
        from app.api.v1.endpoints import blocks as blocks_endpoint
        male_headers, male_id = await register_and_get_headers(
            client, "b3_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "b3_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        _ = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)

        calls = blocks_endpoint.websocket_manager.send_personal_message.await_args_list
        assert len(calls) == 2
        assert any(c.args[0] == str(male_id) for c in calls)
        assert any(c.args[0] == str(female_id) for c in calls)


class TestDeleteChat:
    """1D - Delete/end chat: drops own side, other user sees it ended."""

    async def test_delete_chat_removes_own_list(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "d_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)
        assert res.status_code == 204

        lst = await client.get(CHATS_URL, headers=male_headers)
        assert lst.json()["total"] == 0

    async def test_other_side_sees_ended(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "d2_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d2_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)

        lst = await client.get(CHATS_URL, headers=female_headers)
        conv = lst.json()["chats"][0]
        assert conv["id"] == chat_id
        assert conv["is_ended"] is True

    async def test_send_after_delete_forbidden(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "d3_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d3_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)

        res = await client.post(f"{MESSAGES_URL}/{chat_id}/text", json={"content": "late"}, headers=male_headers)
        assert res.status_code == 403

    async def test_delete_chat_broadcasts_chat_ended(
        self, client, mock_verification_code
    ):
        from app.api.v1.endpoints import chats as chats_endpoint
        male_headers, male_id = await register_and_get_headers(
            client, "d7_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d7_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)

        calls = chats_endpoint.websocket_manager.send_personal_message.await_args_list
        ended = [c for c in calls if c.args[1].get("type") == "chat_ended"]
        assert len(ended) == 1
        assert ended[0].args[1]["data"]["chat_id"] == str(chat_id)

    async def test_owner_can_reopen_after_delete(self, client, mock_verification_code):
        """Deleting only hides your side; you may start a fresh chat later."""
        male_headers, male_id = await register_and_get_headers(
            client, "d4_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d4_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)

        # Starting a fresh chat creates a new active one (not a 403).
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "again"}, headers=male_headers
        )
        assert res.status_code == 200, res.text

    async def test_non_member_cannot_delete(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "d5_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d5_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        stranger_headers, _ = await register_and_get_headers(
            client, "d5_stranger@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.delete(f"{CHATS_URL}/{chat_id}", headers=stranger_headers)
        assert res.status_code == 404

    async def test_delete_own_side_detail_now_403(self, client, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "d6_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "d6_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)
        await client.delete(f"{CHATS_URL}/{chat_id}", headers=male_headers)

        detail = await client.get(f"{CHATS_URL}/{chat_id}", headers=male_headers)
        assert detail.status_code == 403