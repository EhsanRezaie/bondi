
# tests/test_messages.py
import pytest
from httpx import AsyncClient
import base64
from sqlalchemy import select
from sqlalchemy.orm import selectinload
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


from app.models.user import User

VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
CHATS_URL = "/api/v1/chats"
MESSAGES_URL = "/api/v1/messages"

VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD_MALE = {
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

COMPLETE_PROFILE_PAYLOAD_FEMALE = {
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


async def register_and_get_headers(
    client: AsyncClient,
    phone: str,
    complete_payload: dict,
    mock_verification_code
) -> tuple[dict, str]:
    """Register a user via phone OTP and return headers with user_id."""
    await mock_verification_code(phone, VALID_CODE)
    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=complete_payload, headers=headers)
    assert res.status_code == 200, res.text
    result = res.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    user_id = result["user"]["id"]
    return headers, user_id


async def load_profiles(client, db_session, ids):
    """Warm the identity map so profile FKs resolve."""
    result = await db_session.execute(
        select(User).options(selectinload(User.profile)).where(User.id.in_(ids))
    )
    result.scalars().all()


async def make_match(client, male_headers, female_id, female_headers, male_id):
    """Mutual like → start chat (auto-accepted) → return chat_id."""
    await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
    await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)
    res = await client.post(
        CHATS_URL, json={"user_id": female_id, "content": "Hi!"}, headers=male_headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "accepted"
    return res.json()["chat_id"]


def create_test_image() -> bytes:
    """Create a valid minimal JPEG image for testing."""
    return base64.b64decode(
        b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
        b"EBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
        b"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIAAhEBAxEB/8QA"
        b"HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
        b"MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
        b"V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
        b"x8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYH"
        b"CAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy"
        b"0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWG"
        b"h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz"
        b"9PX29/j5+v/aAAwDAQACEQMRAD8A/wB4/wD/2Q=="
    )


def create_test_audio() -> bytes:
    """Create a minimal valid MP3 file for testing."""
    return base64.b64decode(
        b"SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABJbmZvAAAADwAAAEgAAAAMAA=="
    )


class TestMessages:
    """Test basic message functionality."""

    async def test_send_text_message_in_matched_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("chat_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("chat_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text",
            json={"content": "Hello!"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] is not None
        assert data["chat_accepted"] == True

    async def test_get_chat_history(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("hist_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("hist_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Hello"}, headers=male_headers)

        res = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert "messages" in data
        assert "total" in data

    async def test_pending_chat_limit(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        """Initiator can send at most 2 starter messages while chat is pending."""
        male_headers, male_id = await register_and_get_headers(
            client, _phone("pending_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("pending_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])

        # One-sided like → chat created pending (initiator = male, 1 message sent).
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "First message"}, headers=male_headers
        )
        assert res.status_code == 200
        assert res.json()["status"] == "pending"
        chat_id = res.json()["chat_id"]

        # 2nd initiator message (allowed).
        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Second message"}, headers=male_headers
        )
        assert res2.status_code == 200

        # 3rd initiator message → blocked.
        res3 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Third message"}, headers=male_headers
        )
        assert res3.status_code == 403
        assert "must accept" in res3.json()["detail"]

    async def test_accept_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("acc_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("acc_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "Hi"}, headers=male_headers
        )
        chat_id = res.json()["chat_id"]
        assert res.json()["status"] == "pending"

        ares = await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=female_headers)
        assert ares.status_code == 200
        assert ares.json()["status"] == "accepted"

        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text",
            json={"content": "Third message after accept"},
            headers=male_headers,
        )
        assert res2.status_code == 200

    async def test_delete_message_for_me(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("delme_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("delme_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Delete me"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.delete(f"{MESSAGES_URL}/{msg_id}?delete_for=me", headers=male_headers)
        assert res.status_code == 200

    async def test_mark_messages_as_read(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("read_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("read_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Read me"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.post(f"{MESSAGES_URL}/read", json={"message_ids": [msg_id]}, headers=female_headers)
        assert res.status_code == 200

        status_res = await client.get(f"{MESSAGES_URL}/{msg_id}/status", headers=male_headers)
        assert status_res.status_code == 200
        assert status_res.json()["is_read"] == True


class TestPhotoMessages:
    """Test photo message functionality."""

    async def test_send_photo_in_matched_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("ph1_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ph1_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("test.jpg", create_test_image(), "image/jpeg")},
            data={"caption": "Check this!"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] is not None
        assert data["chat_accepted"] == True

    async def test_send_photo_in_pending_chat_fails(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("ph2_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ph2_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hey"}, headers=male_headers
        )
        chat_id = res.json()["chat_id"]

        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("test.jpg", create_test_image(), "image/jpeg")},
            headers=male_headers,
        )
        assert res2.status_code == 403
        assert "accepted chats" in res2.json()["detail"]

    async def test_send_photo_in_accepted_after_accept(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("ph3_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ph3_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "Hi"}, headers=male_headers
        )
        chat_id = res.json()["chat_id"]
        await client.post(f"{CHATS_URL}/{chat_id}/accept", headers=female_headers)

        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("test.jpg", create_test_image(), "image/jpeg")},
            headers=male_headers,
        )
        assert res2.status_code == 200
        assert res2.json()["id"] is not None

    async def test_send_photo_too_large(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("pl_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("pl_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        large_image = b"0" * (6 * 1024 * 1024)
        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("big.jpg", large_image, "image/jpeg")},
            headers=male_headers,
        )
        assert res.status_code == 400
        assert "too large" in res.json()["detail"].lower()


class TestVoiceMessages:
    """Test voice message functionality."""

    async def test_send_voice_in_matched_chat(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("vc_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("vc_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 15},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] is not None
        assert data["chat_accepted"] == True

    async def test_send_voice_in_pending_chat_fails(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("vp_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("vp_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hey"}, headers=male_headers
        )
        chat_id = res.json()["chat_id"]

        res2 = await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 10},
            headers=male_headers,
        )
        assert res2.status_code == 403
        assert "accepted chats" in res2.json()["detail"]

    async def test_send_voice_too_long(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("vl_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("vl_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 150},
            headers=male_headers,
        )
        assert res.status_code == 400
        assert "too long" in res.json()["detail"].lower()


class TestMediaInChatHistory:
    """Test that media appears correctly in chat history."""

    async def test_chat_history_contains_photo(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("m1_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("m1_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            data={"caption": "Test photo"},
            headers=male_headers,
        )

        res = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        data = res.json()
        photo_msg = next((m for m in data["messages"] if m["message_type"] == "photo"), None)
        assert photo_msg is not None
        assert photo_msg["media_url"] is not None
        assert photo_msg["content"] == "Test photo"

    async def test_chat_history_contains_voice(
        self, client: AsyncClient, mock_verification_code, db_session
    ):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("m2_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("m2_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 15},
            headers=male_headers,
        )

        res = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        data = res.json()
        voice_msg = next((m for m in data["messages"] if m["message_type"] == "voice"), None)
        assert voice_msg is not None
        assert voice_msg["media_url"] is not None
        assert voice_msg["media_duration"] == 15


class TestMessageDelivery:
    """Test marking messages as delivered."""

    async def test_mark_delivered_success(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("del_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("del_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Hello"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.post(f"{MESSAGES_URL}/delivered", json={"message_ids": [msg_id]}, headers=female_headers)
        assert res.status_code == 200
        assert "1 messages marked as delivered" in res.json()["message"]

        status_res = await client.get(f"{MESSAGES_URL}/{msg_id}/status", headers=male_headers)
        assert status_res.json()["is_delivered"] is True

    async def test_mark_delivered_requires_auth(self, client):
        res = await client.post(f"{MESSAGES_URL}/delivered", json={"message_ids": []})
        assert res.status_code == 401

    async def test_mark_read_success(self, client, mock_verification_code, db_session):
        """Marking a message read flips is_read and read_at for the receiver."""
        male_headers, male_id = await register_and_get_headers(
            client, _phone("markread_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("markread_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Read me"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.post(f"{MESSAGES_URL}/read", json={"message_ids": [msg_id]}, headers=female_headers)
        assert res.status_code == 200
        assert "messages marked as read" in res.json()["message"]

        status_res = await client.get(f"{MESSAGES_URL}/{msg_id}/status", headers=male_headers)
        assert status_res.status_code == 200
        assert status_res.json()["is_read"] is True
        assert status_res.json()["read_at"] is not None


class TestMessageStatus:
    """Test GET /messages/{message_id}/status."""

    async def test_message_status_shape(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("stat_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("stat_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Status test"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.get(f"{MESSAGES_URL}/{msg_id}/status", headers=male_headers)
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body["id"], str)
        assert isinstance(body["sent_at"], str)
        assert body["delivered_at"] is None
        assert body["read_at"] is None
        assert body["is_delivered"] is False
        assert body["is_read"] is False

    async def test_message_status_unauthorized(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("status2_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("status2_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Status auth"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.get(f"{MESSAGES_URL}/{msg_id}/status", headers=female_headers)
        assert res.status_code == 403


class TestMessageDeleteForEveryone:
    """Test message deletion for everyone."""

    async def test_delete_message_for_everyone(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("delall_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("delall_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Delete for everyone"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.delete(f"{MESSAGES_URL}/{msg_id}?delete_for=everyone", headers=male_headers)
        assert res.status_code == 200

        history = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        assert not any(m["id"] == msg_id for m in history.json()["messages"])

    async def test_delete_message_for_everyone_non_sender(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("delns_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("delns_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Delete test"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.delete(f"{MESSAGES_URL}/{msg_id}?delete_for=everyone", headers=female_headers)
        assert res.status_code == 400

    async def test_delete_message_not_a_member(self, client, mock_verification_code, db_session):
        """A non-participant cannot delete someone else's message."""
        male_headers, male_id = await register_and_get_headers(
            client, _phone("delnm_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("delnm_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, _ = await register_and_get_headers(
            client, _phone("delnm_third@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Mine"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.delete(f"{MESSAGES_URL}/{msg_id}?delete_for=me", headers=third_headers)
        assert res.status_code == 400
        assert "Not authorized to delete this message" in res.json()["detail"]


class TestForwardMessage:
    """Test message forwarding."""

    async def test_forward_message_success(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("fwd_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("fwd_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat1 = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat1}/text", json={"content": "Forward this message"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        # Second chat to forward INTO (any active chat the user belongs to).
        f2_headers, f2_id = await register_and_get_headers(
            client, _phone("fwd_female2@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, f2_id])
        chat2 = await make_match(client, male_headers, f2_id, f2_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{msg_id}/forward",
            json={"target_chat_id": chat2},
            headers=male_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["message"] == "Message forwarded"
        assert "new_message_id" in body

        history = await client.get(f"{MESSAGES_URL}/{chat2}", headers=male_headers)
        assert any(m["id"] == body["new_message_id"] for m in history.json()["messages"])

    async def test_forward_to_nonexistent_chat(self, client, mock_verification_code, db_session):
        from uuid import uuid4
        male_headers, male_id = await register_and_get_headers(
            client, _phone("fwerr_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("fwerr_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Forward error"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        res = await client.post(
            f"{MESSAGES_URL}/{msg_id}/forward",
            json={"target_chat_id": str(uuid4())},
            headers=male_headers,
        )
        assert res.status_code == 400

    async def test_forward_to_chat_not_a_member(self, client, mock_verification_code, db_session):
        """Forwarding into a chat the user does not belong to → 400."""
        from uuid import uuid4
        male_headers, male_id = await register_and_get_headers(
            client, _phone("fwnm_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("fwnm_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        other_headers, other_id = await register_and_get_headers(
            client, _phone("fwnm_other@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id, other_id])
        chat1 = await make_match(client, male_headers, female_id, female_headers, male_id)
        chat2 = await make_match(client, male_headers, other_id, other_headers, male_id)

        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat1}/text", json={"content": "Forward me"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        # female is a member of chat1 but NOT chat2 → cannot forward into chat2
        res = await client.post(
            f"{MESSAGES_URL}/{msg_id}/forward",
            json={"target_chat_id": chat2},
            headers=female_headers,
        )
        assert res.status_code == 400
        assert "Not part of target chat" in res.json()["detail"]

    async def test_forward_message_not_yours(self, client, mock_verification_code, db_session):
        """Forwarding a message you are neither sender nor receiver of → 400."""
        male_headers, male_id = await register_and_get_headers(
            client, _phone("fwnt_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("fwnt_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, third_id = await register_and_get_headers(
            client, _phone("fwnt_third@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id, third_id])
        chat1 = await make_match(client, male_headers, female_id, female_headers, male_id)
        chat2 = await make_match(client, male_headers, third_id, third_headers, male_id)

        # message belongs to chat1 (male ⇄ female); third is NOT a participant of it
        msg_res = await client.post(
            f"{MESSAGES_URL}/{chat1}/text", json={"content": "Secret"}, headers=male_headers
        )
        msg_id = msg_res.json()["id"]

        # third forwards the chat1 message into their own chat2 → rejected
        res = await client.post(
            f"{MESSAGES_URL}/{msg_id}/forward",
            json={"target_chat_id": chat2},
            headers=third_headers,
        )
        assert res.status_code == 400
        assert "Not authorized to forward this message" in res.json()["detail"]


class TestCursorPagination:
    """Test cursor-based pagination with before parameter."""

    async def test_cursor_pagination_returns_older_messages(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("cur1_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("cur1_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        for i in range(5):
            await client.post(
                f"{MESSAGES_URL}/{chat_id}/text", json={"content": f"Message {i}"}, headers=male_headers
            )

        full = await client.get(f"{MESSAGES_URL}/{chat_id}?limit=5", headers=male_headers)
        full_msgs = full.json()["messages"]
        assert len(full_msgs) == 5

        cursor = full_msgs[-1]["sent_at"]
        cursor_res = await client.get(
            f"{MESSAGES_URL}/{chat_id}?before={cursor}&limit=3", headers=male_headers
        )
        assert cursor_res.status_code == 200
        for msg in cursor_res.json()["messages"]:
            assert msg["sent_at"] < cursor

    async def test_cursor_pagination_with_no_older_messages(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("cur2_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("cur2_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Only message"}, headers=male_headers)

        res = await client.get(
            f"{MESSAGES_URL}/{chat_id}?before=2000-01-01T00:00:00Z&limit=10", headers=male_headers
        )
        assert res.status_code == 200
        assert len(res.json()["messages"]) == 0


class TestSendResponseContract:
    """Verify send endpoints return the full message object."""

    async def test_send_text_returns_full_message(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("ct_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ct_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "Contract hello"}, headers=male_headers
        )
        assert res.status_code == 200
        data = res.json()
        msg = data["message"]
        assert msg["content"] == "Contract hello"
        assert msg["sender_id"] == male_id
        assert msg["receiver_id"] == female_id
        assert msg["chat_id"] == chat_id
        assert msg["message_type"] == "text"
        assert msg["id"] == data["id"]


class TestChatMembership:
    """Scenarios: chat access, membership, and missing chats."""

    async def test_send_text_to_nonexistent_chat(self, client, mock_verification_code):
        male_headers, _ = await register_and_get_headers(
            client, _phone("nx_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        res = await client.post(
            f"{MESSAGES_URL}/00000000-0000-0000-0000-000000000aaa/text",
            json={"content": "hello"},
            headers=male_headers,
        )
        assert res.status_code == 404
        assert "Chat not found" in res.json()["detail"]

    async def test_send_text_not_a_member(self, client, mock_verification_code, db_session):
        """A third user cannot send messages in someone else's chat."""
        male_headers, male_id = await register_and_get_headers(
            client, _phone("nmb_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("nmb_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, _ = await register_and_get_headers(
            client, _phone("nmb_third@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text",
            json={"content": "intruder"},
            headers=third_headers,
        )
        assert res.status_code == 404
        assert "Chat not found" in res.json()["detail"]

    async def test_get_history_not_a_member(self, client, mock_verification_code, db_session):
        male_headers, male_id = await register_and_get_headers(
            client, _phone("nh_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("nh_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        third_headers, _ = await register_and_get_headers(
            client, _phone("nh_third@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=third_headers)
        assert res.status_code == 404

    async def test_send_photo_to_nonexistent_chat(self, client, mock_verification_code):
        male_headers, _ = await register_and_get_headers(
            client, _phone("nxp_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        res = await client.post(
            f"{MESSAGES_URL}/00000000-0000-0000-0000-000000000bbb/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            headers=male_headers,
        )
        assert res.status_code == 404

    async def test_message_status_nonexistent(self, client, mock_verification_code):
        male_headers, _ = await register_and_get_headers(
            client, _phone("nxs_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        res = await client.get(
            f"{MESSAGES_URL}/00000000-0000-0000-0000-000000000ccc/status",
            headers=male_headers,
        )
        assert res.status_code == 404


class TestChatListRealTime:
    """Real-time chat_updated events on the recipient's personal channel."""

    async def test_chat_updated_on_text(
        self, client, mock_verification_code, db_session
    ):
        from app.api.v1.endpoints.messages import websocket_manager as ws_mock

        ws_mock.send_personal_message.reset_mock()

        male_headers, male_id = await register_and_get_headers(
            client, _phone("rt_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("rt_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/text",
            json={"content": "rt hello"},
            headers=male_headers,
        )

        calls = [c for c in ws_mock.send_personal_message.call_args_list if c[0]]
        assert len(calls) == 1
        user_id, payload = calls[0].args[0], calls[0].args[1]
        assert user_id == str(female_id)
        assert payload["type"] == "chat_updated"
        data = payload["data"]
        assert data["chat_id"] == str(chat_id)
        assert data["status"] == "accepted"
        assert data["last_message"]["content"] == "rt hello"
        assert data["last_message"]["message_type"] == "text"
        assert data["unread_count"] == 2

    async def test_chat_updated_on_photo(
        self, client, mock_verification_code, db_session
    ):
        from app.api.v1.endpoints.messages import websocket_manager as ws_mock

        ws_mock.send_personal_message.reset_mock()

        male_headers, male_id = await register_and_get_headers(
            client, _phone("rtp_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("rtp_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            headers=male_headers,
        )

        calls = [c for c in ws_mock.send_personal_message.call_args_list if c[0]]
        assert len(calls) == 1
        user_id, payload = calls[0].args[0], calls[0].args[1]
        assert user_id == str(female_id)
        assert payload["type"] == "chat_updated"
        assert payload["data"]["last_message"]["message_type"] == "photo"

    async def test_chat_updated_on_voice(
        self, client, mock_verification_code, db_session
    ):
        from app.api.v1.endpoints.messages import websocket_manager as ws_mock

        ws_mock.send_personal_message.reset_mock()

        male_headers, male_id = await register_and_get_headers(
            client, _phone("rtv_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("rtv_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 3},
            headers=male_headers,
        )

        calls = [c for c in ws_mock.send_personal_message.call_args_list if c[0]]
        assert len(calls) == 1
        user_id, payload = calls[0].args[0], calls[0].args[1]
        assert user_id == str(female_id)
        assert payload["type"] == "chat_updated"
        assert payload["data"]["last_message"]["message_type"] == "voice"

    async def test_chat_updated_unread_count(self, client, mock_verification_code, db_session):
        """unread_count counts only unread messages addressed to the recipient."""
        from app.api.v1.endpoints.messages import websocket_manager as ws_mock

        ws_mock.send_personal_message.reset_mock()

        male_headers, male_id = await register_and_get_headers(
            client, _phone("rtu_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("rtu_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "one"}, headers=male_headers
        )
        await client.post(
            f"{MESSAGES_URL}/{chat_id}/text", json={"content": "two"}, headers=male_headers
        )

        calls = [c for c in ws_mock.send_personal_message.call_args_list if c[0]]
        assert len(calls) == 2
        last = calls[-1].args[1]["data"]
        assert last["unread_count"] == 3

# =============================================================================
# client_id round-trip (optimistic-send dedup)
# =============================================================================

class TestClientIdRoundTrip:

    async def test_text_send_echoes_client_id(self, client, mock_verification_code, db_session):
        import uuid
        from app.models.message import Message

        male_headers, male_id = await register_and_get_headers(
            client, _phone("cid_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("cid_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        cid = uuid.uuid4()
        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/text",
            json={"content": "dedup me", "client_id": str(cid)},
            headers=male_headers,
        )
        assert res.status_code == 200
        assert res.json()["message"]["client_id"] == str(cid)

        hist = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        row = next(
            m for m in hist.json()["messages"]
            if m["message_type"] == "text" and m["content"] == "dedup me"
        )
        assert row["client_id"] == str(cid)

        db_row = (
            await db_session.execute(
                select(Message).where(
                    Message.chat_id == chat_id, Message.client_id == cid
                )
            )
        ).scalars().first()
        assert db_row is not None
        assert str(db_row.client_id) == str(cid)

    async def test_photo_send_echoes_client_id(self, client, mock_verification_code, db_session):
        import uuid
        male_headers, male_id = await register_and_get_headers(
            client, _phone("cidp_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("cidp_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        cid = uuid.uuid4()
        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            data={"caption": "x", "client_id": str(cid)},
            headers=male_headers,
        )
        assert res.status_code == 200
        assert res.json()["message"]["client_id"] == str(cid)


# =============================================================================
# Chat media: store key, re-sign at read time
# =============================================================================

class TestMediaReSign:

    async def test_photo_stores_key_returns_signed_url(self, client, mock_verification_code, db_session):
        from app.models.message import Message

        male_headers, male_id = await register_and_get_headers(
            client, _phone("ms1_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ms1_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            data={"caption": "photo"},
            headers=male_headers,
        )
        assert res.status_code == 200
        signed = res.json()["message"]["media_url"]
        # Response carries a loadable signed URL...
        assert signed.startswith("http")
        assert "X-Amz-Signature" in signed or "Signature" in signed

        # ...but the DB row stores the object key (no expiry).
        db_row = (
            await db_session.execute(
                select(Message).where(
                    Message.chat_id == chat_id, Message.message_type == "photo"
                )
            )
        ).scalars().first()
        assert db_row is not None
        assert db_row.media_url.startswith("chat/photos/")

        # History also returns a freshly-signed URL, not the key.
        hist = await client.get(f"{MESSAGES_URL}/{chat_id}", headers=male_headers)
        photo = next(m for m in hist.json()["messages"] if m["message_type"] == "photo")
        assert photo["media_url"].startswith("http")

    async def test_voice_stores_key(self, client, mock_verification_code, db_session):
        from app.models.message import Message

        male_headers, male_id = await register_and_get_headers(
            client, _phone("ms2_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("ms2_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        res = await client.post(
            f"{MESSAGES_URL}/{chat_id}/voice",
            files={"file": ("v.mp3", create_test_audio(), "audio/mpeg")},
            data={"duration": 15},
            headers=male_headers,
        )
        assert res.status_code == 200
        assert res.json()["message"]["media_url"].startswith("http")

        db_row = (
            await db_session.execute(
                select(Message).where(
                    Message.chat_id == chat_id, Message.message_type == "voice"
                )
            )
        ).scalars().first()
        assert db_row is not None
        assert db_row.media_url.startswith("chat/voice/")


class TestMediaResolve:

    async def test_resolve_key_returns_signed_url(self):
        from app.services.media_service import MediaService
        url = await MediaService.resolve_media_url("chat/photos/c1/m1.jpg")
        assert url.startswith("http")
        assert "X-Amz-Signature" in url or "Signature" in url

    async def test_resolve_legacy_url_extracts_key_and_resigns(self):
        from app.services.media_service import MediaService
        legacy = "http://localhost:9090/photos-private/chat/voice/c1/m1.mp3?X-Amz-Signature=stale"
        url = await MediaService.resolve_media_url(legacy)
        assert url.startswith("http")
        assert url != legacy

    async def test_resolve_none_returns_none(self):
        from app.services.media_service import MediaService
        assert await MediaService.resolve_media_url(None) is None

    async def test_resolve_caches_url(self):
        import app.core.redis as redis_module
        from app.services.media_service import MediaService

        key = "chat/photos/cachekey/m1.jpg"
        r = redis_module.redis_client
        await r.delete(f"chat_media:{key}")
        url1 = await MediaService.resolve_media_url(key)
        assert url1.startswith("http")
        # Second call must hit the Redis cache (same URL, no re-sign).
        url2 = await MediaService.resolve_media_url(key)
        assert url2 == url1


class TestBackfillMediaUrls:

    async def test_extract_key(self):
        from scripts.backfill_chat_media_urls import extract_key
        assert extract_key("http://minio:9000/photos-private/chat/photos/c1/m1.jpg?x=1") == "chat/photos/c1/m1.jpg"
        assert extract_key("chat/photos/c1/m1.jpg") == "chat/photos/c1/m1.jpg"

    async def test_backfill_converts_legacy_rows(self, client, mock_verification_code, db_session):
        from app.models.message import Message
        from scripts.backfill_chat_media_urls import backfill

        male_headers, male_id = await register_and_get_headers(
            client, _phone("bk_male@example.com"), COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, _phone("bk_female@example.com"), COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await load_profiles(client, db_session, [male_id, female_id])
        chat_id = await make_match(client, male_headers, female_id, female_headers, male_id)

        await client.post(
            f"{MESSAGES_URL}/{chat_id}/photo",
            files={"file": ("t.jpg", create_test_image(), "image/jpeg")},
            data={"caption": "x"},
            headers=male_headers,
        )

        db_row = (
            await db_session.execute(
                select(Message).where(
                    Message.chat_id == chat_id, Message.message_type == "photo"
                )
            )
        ).scalars().first()
        key = db_row.media_url
        assert key.startswith("chat/photos/")

        # Corrupt it back into the old presigned-URL form, then backfill.
        db_row.media_url = f"http://localhost:9090/photos-private/{key}?X-Amz-Signature=stale"
        await db_session.commit()

        result = await backfill(dry_run=False)
        assert result["updated"] >= 1

        fresh = (
            await db_session.execute(
                select(Message)
                .where(Message.id == db_row.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert fresh.media_url == key
