# tests/done/test_conversations.py
import uuid
from httpx import AsyncClient

from app.models.block import Block

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
MESSAGES_URL = "/api/v1/messages"
CONVERSATIONS_URL = "/api/v1/conversations"

VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

PAYLOAD_MALE = {
    "name": "Conv Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
}

PAYLOAD_FEMALE = {
    "name": "Conv Female",
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
        "email": email,
        "code": VALID_CODE,
        "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=complete_payload, headers=headers)
    assert res.status_code == 200, res.text
    result = res.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    return headers, result["user"]["id"]


class TestConversations:

    async def test_empty_when_no_chats(
        self, client: AsyncClient, mock_verification_code
    ):
        headers, _ = await register_and_get_headers(
            client, "conv_empty@example.com", PAYLOAD_MALE, mock_verification_code
        )
        res = await client.get(CONVERSATIONS_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["conversations"] == []
        assert data["total"] == 0

    async def test_unmatched_conversation_appears_after_like_and_message(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        # Like + send a message (unmatched chat — no mutual like)
        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        res = await client.post(
            f"{MESSAGES_URL}/{female_id}/text",
            json={"content": "Hey there"},
            headers=male_headers,
        )
        assert res.status_code == 200

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        assert conv_res.status_code == 200
        data = conv_res.json()
        assert data["total"] == 1
        conv = data["conversations"][0]
        assert conv["kind"] == "unmatched"
        assert conv["id"] == female_id
        assert conv["user"]["id"] == female_id
        assert conv["last_message"]["content"] == "Hey there"
        assert conv["last_message"]["is_sent"] is True

    async def test_matched_conversation_appears(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_match_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_match_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        match_res = await client.post(
            SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers
        )
        match_id = match_res.json()["match_id"]

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        assert conv_res.status_code == 200
        data = conv_res.json()
        assert data["total"] == 1
        conv = data["conversations"][0]
        assert conv["kind"] == "match"
        assert conv["id"] == match_id
        assert conv["is_accepted"] is True

    async def test_sorted_by_latest_message_desc(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_sort_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        f1_headers, f1_id = await register_and_get_headers(
            client, "conv_sort_f1@example.com", PAYLOAD_FEMALE, mock_verification_code
        )
        f2_headers, f2_id = await register_and_get_headers(
            client, "conv_sort_f2@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": f1_id, "direction": "like"}, headers=male_headers
        )
        await client.post(
            SWIPE_URL, json={"user_id": f2_id, "direction": "like"}, headers=male_headers
        )
        await client.post(f"{MESSAGES_URL}/{f1_id}/text", json={"content": "first"}, headers=male_headers)
        await client.post(f"{MESSAGES_URL}/{f2_id}/text", json={"content": "second"}, headers=male_headers)

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        data = conv_res.json()
        assert data["total"] == 2
        assert data["conversations"][0]["id"] == f2_id  # newest message first
        assert data["conversations"][1]["id"] == f1_id

    async def test_pagination(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_page_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        ids = []
        for i in range(3):
            _, uid = await register_and_get_headers(
                client,
                f"conv_page_{i}@example.com",
                {**PAYLOAD_FEMALE, "name": f"Page {i}"},
                mock_verification_code,
            )
            ids.append(uid)
            await client.post(
                SWIPE_URL, json={"user_id": uid, "direction": "like"}, headers=male_headers
            )
            await client.post(f"{MESSAGES_URL}/{uid}/text", json={"content": f"msg {i}"}, headers=male_headers)

        res1 = await client.get(CONVERSATIONS_URL, params={"limit": 2, "offset": 0}, headers=male_headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["total"] == 3
        assert len(data1["conversations"]) == 2
        assert data1["next_offset"] == 2

        res2 = await client.get(CONVERSATIONS_URL, params={"limit": 2, "offset": 2}, headers=male_headers)
        data2 = res2.json()
        assert len(data2["conversations"]) == 1
        assert data2["next_offset"] is None

    async def test_requires_auth(self, client: AsyncClient):
        res = await client.get(CONVERSATIONS_URL)
        assert res.status_code == 401

    async def test_is_online_and_last_seen(
        self, client: AsyncClient, mock_verification_code
    ):
        import app.core.redis as redis_module

        male_headers, male_id = await register_and_get_headers(
            client, "conv_online_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_online_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        await client.post(f"{MESSAGES_URL}/{female_id}/text", json={"content": "hi"}, headers=male_headers)

        # Simulate the other user being online in Redis
        await redis_module.redis_client.setex(f"online:{female_id}", 60, "1")

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        data = conv_res.json()
        conv = data["conversations"][0]
        assert conv["user"]["is_online"] is True
        assert "last_seen_at" in conv["user"]

    async def test_blocked_user_excluded(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_block_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_block_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        await client.post(f"{MESSAGES_URL}/{female_id}/text", json={"content": "hi"}, headers=male_headers)

        # Male blocks female
        db_session.add(Block(blocker_id=uuid.UUID(male_id), blocked_id=uuid.UUID(female_id)))
        await db_session.commit()

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        data = conv_res.json()
        assert data["total"] == 0
        assert data["conversations"] == []

    async def test_unread_count(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_unread_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_unread_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        # Female messages male (unmatched thread)
        await client.post(
            SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers
        )
        await client.post(f"{MESSAGES_URL}/{male_id}/text", json={"content": "for you"}, headers=female_headers)

        # Male sees 1 unread
        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        data = conv_res.json()
        conv = data["conversations"][0]
        assert conv["unread_count"] == 1
        assert conv["last_message"]["is_sent"] is False

    async def test_deleted_for_all_thread_excluded(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, male_id = await register_and_get_headers(
            client, "conv_del_male@example.com", PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "conv_del_female@example.com", PAYLOAD_FEMALE, mock_verification_code
        )

        await client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers
        )
        res = await client.post(
            f"{MESSAGES_URL}/{female_id}/text", json={"content": "to delete"}, headers=male_headers
        )
        msg_id = res.json()["message"]["id"]

        # Delete for everyone — thread should disappear from conversations
        await client.delete(f"{MESSAGES_URL}/{msg_id}?delete_for=everyone", headers=male_headers)

        conv_res = await client.get(CONVERSATIONS_URL, headers=male_headers)
        data = conv_res.json()
        assert data["total"] == 0
