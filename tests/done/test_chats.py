# tests/test_chats.py
import uuid
from datetime import date, timedelta, datetime, timezone
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.block import Block
from app.models.daily_limit import DailyLimit
from app.models.message import Message
from app.core.config import settings
import app.core.redis as redis_module

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
CHATS_URL = "/api/v1/chats"
MESSAGES_URL = "/api/v1/messages"

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
}

PAYLOAD_FEMALE = {
    "name": "Chat Female",
    "birth_date": "2000-06-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
}


async def register_and_get_headers(
    client: AsyncClient,
    email: str,
    complete_payload: dict,
    mock_verification_code,
) -> tuple[dict, str]:
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text
    await mock_verification_code(email, VALID_CODE)
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email, "code": VALID_CODE, "password": "strongpass123",
    })
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=complete_payload, headers=headers)
    assert res.status_code == 200, res.text
    result = res.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    return headers, result["user"]["id"]


class TestCreateChat:

    async def test_create_pending_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """One-sided like → chat created as 'pending' with first message."""
        male_headers, male_id = await register_and_get_headers(
            client, "chats_p_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "chats_p_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)

        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "Salam!"}, headers=male_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_new"] is True
        assert data["status"] == "pending"
        assert data["chat_id"] is not None
        assert data["message"]["content"] == "Salam!"

        # Chat appears in list for both users
        for headers in (male_headers, female_headers):
            lst = await client.get(CHATS_URL, headers=headers)
            assert lst.status_code == 200
            assert lst.json()["total"] == 1
            assert lst.json()["chats"][0]["id"] == data["chat_id"]

    async def test_create_chat_accepted_on_mutual_like(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """Mutual like (a real match) → chat created already 'accepted'."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_a_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_a_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)

        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hello"}, headers=male_headers
        )
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"

    async def test_existing_chat_is_returned_not_created(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """Starting a chat with the same user returns the existing one (is_new=false)."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_e_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_e_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )

        first = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "first"}, headers=male_headers
        )
        assert first.json()["is_new"] is True
        chat_id = first.json()["chat_id"]

        second = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "second"}, headers=male_headers
        )
        assert second.status_code == 200
        assert second.json()["is_new"] is False
        assert second.json()["chat_id"] == chat_id
        assert second.json().get("message") is None

        # Only one message should exist in the chat (the first)
        count = await db_session.scalar(
            select(Message).where(Message.chat_id == uuid.UUID(chat_id))
        ) is not None
        msgs = (await db_session.execute(
            select(Message.id).where(Message.chat_id == uuid.UUID(chat_id))
        )).scalars().all()
        assert len(msgs) == 1

    async def test_chat_with_self_rejected(self, client: AsyncClient, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_self@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.post(
            CHATS_URL, json={"user_id": male_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code == 400

    async def test_chat_with_unknown_user(self, client: AsyncClient, mock_verification_code):
        male_headers, _ = await register_and_get_headers(
            client, "ch_unknown@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.post(
            CHATS_URL,
            json={"user_id": "00000000-0000-0000-0000-000000000999", "content": "hi"},
            headers=male_headers,
        )
        assert res.status_code == 404

    async def test_chat_with_blocked_user(self, client: AsyncClient, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_block_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_block_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        db_session.add(Block(blocker_id=uuid.UUID(male_id), blocked_id=uuid.UUID(female_id)))
        await db_session.commit()

        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code == 403

    async def test_new_chat_requires_auth(self, client: AsyncClient):
        res = await client.post(CHATS_URL, json={"user_id": str(uuid.uuid4()), "content": "hi"})
        assert res.status_code == 401

    async def test_daily_limit_blocks_new_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """A non-premium user at their daily chat limit gets 429 on a NEW chat."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_limit_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_limit_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )

        # Make male a non-premium user.
        profile = (await db_session.execute(
            select(UserProfile).where(UserProfile.user_id == uuid.UUID(male_id))
        )).scalar_one()
        profile.premium_until = None

        # Pre-fill today's daily limit so no chats remain.
        db_session.add(DailyLimit(
            user_id=uuid.UUID(male_id),
            date=date.today(),
            likes_used=0,
            chats_used=settings.FREE_USER_DAILY_CHATS,
            ad_likes_bonus=0,
            ad_chats_bonus=0,
        ))
        await db_session.commit()

        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code == 429
        assert "limit" in res.json()["detail"]


class TestAcceptChat:

    async def test_recipient_accepts_pending_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_accept_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_accept_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)

        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hey"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]
        assert created.json()["status"] == "pending"

        # Initiator can't accept their own chat.
        unauth = await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=male_headers)
        assert unauth.status_code == 403

        # Recipient can accept.
        res = await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=female_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"

        # After acceptance the initiator can keep sending (nice-to-have check).
        msg = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "thanks!"}, headers=male_headers
        )
        assert msg.status_code == 200

    async def test_pending_initiator_limit(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """While pending, the initiator can send at most 2 messages total."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_limit_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_limit_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)

        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "one"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]  # 1st message

        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "two"}, headers=male_headers
        )
        assert res2.status_code == 200  # 2nd message

        res3 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "three"}, headers=male_headers
        )
        assert res3.status_code == 403
        assert "must accept" in res3.json()["detail"]

    async def test_pending_photo_rejected(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_ph_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_ph_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)

        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hey"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", b"x" * 100, "image/jpeg")},
            headers=male_headers,
        )
        assert res.status_code == 403
        assert "accepted chats" in res.json()["detail"]

    async def test_accept_nonexistent_chat(
        self, client: AsyncClient, mock_verification_code
    ):
        """Accepting a chat id that doesn't exist → 404."""
        male_headers, _ = await register_and_get_headers(
            client, "ch_accn_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.post(
            f"{CHATS_URL}/00000000-0000-0000-0000-000000000777/accept",
            headers=male_headers,
        )
        assert res.status_code == 404

    async def test_accept_already_accepted_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """Accepting an already-accepted chat returns 200 'already accepted'."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_aa_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_aa_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)

        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]
        assert created.json()["status"] == "accepted"

        res = await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=female_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"
        assert "already accepted" in res.json()["message"].lower()

    async def test_accept_requires_auth(
        self, client: AsyncClient, mock_verification_code
    ):
        """Unauthenticated accept → 401."""
        res = await client.post(
            f"{CHATS_URL}/00000000-0000-0000-0000-000000000000/accept"
        )
        assert res.status_code == 401

    async def test_accept_publishes_personal_chat_accepted(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """Accepting a chat publishes chat_accepted to BOTH users' personal channels."""
        from app.api.v1.endpoints import chats as chats_module

        male_headers, male_id = await register_and_get_headers(
            client, "ch_ca_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_ca_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]
        assert created.json()["status"] == "pending"

        with patch.object(chats_module.websocket_manager, "send_personal_message", new_callable=AsyncMock) as mock_send:
            res = await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=female_headers)
            assert res.status_code == 200
            assert mock_send.await_count == 2
            sent = {c.args[0]: c.args[1] for c in mock_send.await_args_list}
            assert set(sent.keys()) == {str(male_id), str(female_id)}
            for user_id, payload in sent.items():
                assert payload["type"] == "chat_accepted"
                assert payload["data"]["chat_id"] == str(chat_id)
                assert payload["data"]["status"] == "accepted"
                assert payload["data"]["accepted_by"] == str(female_id)


class TestChatList:

    async def test_empty(self, client: AsyncClient, mock_verification_code):
        headers, _ = await register_and_get_headers(
            client, "ch_lst_empty@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.get(CHATS_URL, headers=headers)
        assert res.status_code == 200
        assert res.json()["chats"] == []
        assert res.json()["total"] == 0

    async def test_list_requires_auth(self, client: AsyncClient):
        assert (await client.get(CHATS_URL)).status_code == 401

    async def test_unread_count(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_unread_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_unread_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        # Female starts the chat with male (initiator).
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        created = await client.post(
            CHATS_URL, json={"user_id": male_id, "content": "for you"}, headers=female_headers
        )
        chat_id = created.json()["chat_id"]

        lst = await client.get(CHATS_URL, headers=male_headers)
        conv = lst.json()["chats"][0]
        assert conv["id"] == chat_id
        assert conv["unread_count"] >= 1
        assert conv["last_message"]["is_sent"] is False

    async def test_blocked_user_excluded(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_b_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_b_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers)

        db_session.add(Block(blocker_id=uuid.UUID(male_id), blocked_id=uuid.UUID(female_id)))
        await db_session.commit()

        lst = await client.get(CHATS_URL, headers=male_headers)
        assert lst.json()["total"] == 1
        assert lst.json()["chats"][0]["is_blocked"] is True

    async def test_sorted_by_latest_activity(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, _ = await register_and_get_headers(
            client, "ch_sort_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        f1, f1_id = await register_and_get_headers(
            client, "ch_sort_f1@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        f2, f2_id = await register_and_get_headers(
            client, "ch_sort_f2@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        await client.post(SWIPE_URL, json={"user_id": f1_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": f2_id, "direction": "like"}, headers=male_headers)

        c1 = await client.post(CHATS_URL, json={"user_id": f1_id, "content": "first"}, headers=male_headers)
        c2 = await client.post(CHATS_URL, json={"user_id": f2_id, "content": "second"}, headers=male_headers)
        chat2 = c2.json()["chat_id"]

        # Send a follow-up to chat2 so it sorts newest.
        await client.post(f"{MESSAGES_URL}/{chat2}/text", json={"content": "again"}, headers=male_headers)

        lst = await client.get(CHATS_URL, headers=male_headers)
        assert lst.json()["total"] == 2
        assert lst.json()["chats"][0]["id"] == chat2  # newest activity first

    async def test_pagination(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_page_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        for i in range(3):
            _, uid = await register_and_get_headers(
                client,
                f"ch_page_{i}@example.com",
                {**PAYLOAD_FEMALE, "name": f"Page {i}"},
                mock_verification_code,
            )
            await client.post(
                SWIPE_URL, json={"user_id": uid, "direction": "like"}, headers=male_headers
            )
            await client.post(CHATS_URL, json={"user_id": uid, "content": f"msg {i}"}, headers=male_headers)

        res1 = await client.get(CHATS_URL, params={"limit": 2, "offset": 0}, headers=male_headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["total"] == 3
        assert len(data1["chats"]) == 2
        assert data1["next_offset"] == 2

        res2 = await client.get(CHATS_URL, params={"limit": 2, "offset": 2}, headers=male_headers)
        data2 = res2.json()
        assert len(data2["chats"]) == 1
        assert data2["next_offset"] is None

    async def test_is_online_and_last_seen(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_online_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_online_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        await client.post(CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers)

        await redis_module.redis_client.setex(f"online:{female_id}", 60, "1")

        lst = await client.get(CHATS_URL, headers=male_headers)
        conv = lst.json()["chats"][0]
        assert conv["user"]["is_online"] is True
        assert "last_seen_at" in conv["user"]

    async def test_list_status_filter(
        self, client: AsyncClient, mock_verification_code
    ):
        """status=accepted|pending filters the list; total reflects the filtered set."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_filter_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_filter_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, third_id = await register_and_get_headers(
            client, "ch_filter_third@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )

        # Pending chat: male → female (one-sided swipe → pending).
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        pending = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        assert pending.json()["status"] == "pending"

        # Accepted chat: male → third with mutual like.
        await client.post(SWIPE_URL, json={"user_id": third_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=third_headers)
        accepted = await client.post(
            CHATS_URL, json={"user_id": third_id, "content": "hello"}, headers=male_headers
        )
        assert accepted.json()["status"] == "accepted"

        all_chats = (await client.get(CHATS_URL, headers=male_headers)).json()
        assert all_chats["total"] == 2

        pend = (await client.get(CHATS_URL, params={"status": "pending"}, headers=male_headers)).json()
        assert pend["total"] == 1
        assert len(pend["chats"]) == 1
        assert pend["chats"][0]["id"] == pending.json()["chat_id"]

        acc = (await client.get(CHATS_URL, params={"status": "accepted"}, headers=male_headers)).json()
        assert acc["total"] == 1
        assert len(acc["chats"]) == 1
        assert acc["chats"][0]["id"] == accepted.json()["chat_id"]

        # Invalid status → 422.
        bad = await client.get(CHATS_URL, params={"status": "bogus"}, headers=male_headers)
        assert bad.status_code == 422

    async def test_list_initiator_id(
        self, client: AsyncClient, mock_verification_code
    ):
        """initiator_id is exposed so the client can split pending chats by direction."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_init_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_init_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )

        # Male starts the chat (initiator).
        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]

        for headers in (male_headers, female_headers):
            lst = await client.get(CHATS_URL, params={"status": "pending"}, headers=headers)
            assert lst.status_code == 200
            assert lst.json()["total"] == 1
            chat = lst.json()["chats"][0]
            assert chat["id"] == chat_id
            # Initiator is the one who started the chat (male), regardless of viewer.
            assert chat["initiator_id"] == male_id

    async def test_list_status_filter_pagination(
        self, client: AsyncClient, mock_verification_code
    ):
        """Pagination (limit/offset/next_offset) works on the filtered set."""
        male_headers, male_id = await register_and_get_headers(
            client, "ch_fpg_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        for i in range(3):
            _, uid = await register_and_get_headers(
                client,
                f"ch_fpg_{i}@demo.com",
                {**PAYLOAD_FEMALE, "name": f"FPG {i}"},
                mock_verification_code,
            )
            await client.post(
                SWIPE_URL, json={"user_id": uid, "direction": "like"}, headers=male_headers
            )
            await client.post(CHATS_URL, json={"user_id": uid, "content": f"msg {i}"}, headers=male_headers)

        res1 = await client.get(
            CHATS_URL, params={"status": "pending", "limit": 2, "offset": 0}, headers=male_headers
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["total"] == 3
        assert len(data1["chats"]) == 2
        assert data1["next_offset"] == 2
        assert all(c["status"] == "pending" for c in data1["chats"])

        res2 = await client.get(
            CHATS_URL, params={"status": "pending", "limit": 2, "offset": 2}, headers=male_headers
        )
        data2 = res2.json()
        assert len(data2["chats"]) == 1
        assert data2["next_offset"] is None


class TestChatDetail:

    async def test_get_detail(self, client: AsyncClient, mock_verification_code):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_d_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_d_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]

        res = await client.get(f"{CHATS_URL}/{chat_id}", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == chat_id
        assert data["status"] == "pending"
        assert data["user"]["id"] == female_id

    async def test_get_detail_forbidden(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "ch_d2_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "ch_d2_female@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, _ = await register_and_get_headers(
            client, "ch_d2_third@demo.com", PAYLOAD_FEMALE, mock_verification_code
        )
        created = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = created.json()["chat_id"]

        res = await client.get(f"{CHATS_URL}/{chat_id}", headers=third_headers)
        assert res.status_code == 404

    async def test_get_detail_nonexistent(
        self, client: AsyncClient, mock_verification_code
    ):
        """Detail of a chat that doesn't exist → 404."""
        male_headers, _ = await register_and_get_headers(
            client, "ch_dn_male@demo.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.get(
            f"{CHATS_URL}/00000000-0000-0000-0000-000000000888", headers=male_headers
        )
        assert res.status_code == 404


class TestChatsCursorPagination:
    """Keyset cursor pagination for the chat list must never return the same
    conversation twice — even when a chat's activity (updated_at) moves it up
    the list between page loads."""

    async def _create_pending_chat(
        self,
        client: AsyncClient,
        male_headers: dict,
        female_email: str,
        index: int,
        mock_verification_code,
    ) -> str:
        """Register a female and create a pending chat with the male."""
        female_headers, female_id = await register_and_get_headers(
            client, female_email, {**PAYLOAD_FEMALE, "name": f"Chat Cursor {index}"},
            mock_verification_code,
        )
        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code == 200, res.text
        return res.json()["chat_id"]

    async def _walk_pages(
        self, client: AsyncClient, headers: dict, limit: int, start_cursor: str | None = None
    ) -> tuple[dict, list[str]]:
        all_ids: list[str] = []
        cursor = start_cursor
        while True:
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            res = await client.get(CHATS_URL, params=params, headers=headers)
            assert res.status_code == 200, res.text
            data = res.json()
            page_ids = [c["id"] for c in data["chats"]]
            assert len(page_ids) <= limit
            all_ids.extend(page_ids)
            cursor = data.get("next_cursor")
            if not cursor:
                return data, all_ids

    async def test_cursor_walks_all_pages_without_duplicates(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await register_and_get_headers(
            client, "cursor_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_ids = []
        for i in range(4):
            chat_ids.append(await self._create_pending_chat(
                client, male_headers, f"cursor_chat{i}@example.com", i, mock_verification_code
            ))

        data, all_ids = await self._walk_pages(client, male_headers, limit=2)
        assert data["total"] == 4
        assert len(all_ids) == 4
        assert len(set(all_ids)) == 4
        assert set(all_ids) == set(chat_ids)

    async def test_cursor_no_duplicates_when_chat_moves_up(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """A new message bumping a chat to the top between pages must NOT make
        it appear again on a later page (the offset-pagination chat bug)."""
        male_headers, _ = await register_and_get_headers(
            client, "cursor_shift_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_ids = []
        for i in range(4):
            chat_ids.append(await self._create_pending_chat(
                client, male_headers, f"cursor_shift{i}@example.com", i, mock_verification_code
            ))

        res1 = await client.get(CHATS_URL, params={"limit": 2}, headers=male_headers)
        assert res1.status_code == 200
        p1 = res1.json()
        page1_ids = {c["id"] for c in p1["chats"]}
        cursor = p1["next_cursor"]
        assert cursor

        # Simulate a fresh message arriving in one of the chats already shown:
        # its sort key (last message sent_at) jumps to 'now', moving it to the top.
        bumped_chat = next(iter(page1_ids))
        await db_session.execute(
            update(Message)
            .where(Message.chat_id == bumped_chat)
            .values(sent_at=datetime.now(timezone.utc) + timedelta(seconds=30))
        )
        await db_session.commit()

        data, later_ids = await self._walk_pages(
            client, male_headers, limit=2, start_cursor=cursor
        )
        assert len(set(later_ids)) == len(later_ids)  # no internal dupes
        assert page1_ids.isdisjoint(set(later_ids))  # bumped chat NOT re-returned
        assert page1_ids | set(later_ids) == set(chat_ids)  # every chat exactly once

    async def test_cursor_stable_when_sort_keys_tie(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, _ = await register_and_get_headers(
            client, "cursor_tie_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        chat_ids = []
        for i in range(4):
            chat_ids.append(await self._create_pending_chat(
                client, male_headers, f"cursor_tie{i}@example.com", i, mock_verification_code
            ))

        fixed = datetime(2024, 1, 1, tzinfo=timezone.utc)
        await db_session.execute(
            update(Message)
            .where(Message.chat_id.in_(chat_ids))
            .values(sent_at=fixed)
        )
        await db_session.commit()

        data, all_ids = await self._walk_pages(client, male_headers, limit=2)
        assert data["total"] == 4
        assert len(all_ids) == 4
        assert len(set(all_ids)) == 4

    async def test_invalid_cursor_falls_back_to_offset(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await register_and_get_headers(
            client, "cursor_bad_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        for i in range(3):
            await self._create_pending_chat(
                client, male_headers, f"cursor_bad{i}@example.com", i, mock_verification_code
            )

        res = await client.get(
            CHATS_URL,
            params={"limit": 2, "cursor": "::not-a-real-cursor::"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["chats"]) >= 1
        assert data["next_offset"] == 2
        assert data["next_cursor"] is not None