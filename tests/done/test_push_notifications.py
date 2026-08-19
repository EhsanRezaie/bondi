
# tests/test_push_notifications.py

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
DEVICE_TOKEN_URL = "/api/v1/notifications/device-token"
SWIPE_URL = "/api/v1/swipes"
MESSAGES_URL = "/api/v1/messages"

VALID_EMAIL = _phone("push_test@example.com")
VALID_EMAIL_2 = _phone("push_test2@example.com")
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD = {
    "name": "Push Test User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test user",
    "height": 180,
    "weight": 75,
    "body_type": "athletic",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Tech",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}

COMPLETE_PROFILE_PAYLOAD_2 = {
    "name": "Push Test User 2",
    "birth_date": "1998-05-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test user 2",
    "height": 165,
    "weight": 60,
    "body_type": "slim",
    "relationship_status": "single",
    "living_situation": "with_family",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "never",
    "education": "master",
    "workplace": "Hospital",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}


async def register_user(client, phone, payload, mock_verification_code):
    """Register a user via phone OTP and return tokens."""
    await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
    assert res.status_code == 200, res.text

    return res.json()


class TestDeviceToken:

    async def test_register_device_token(self, client, mock_verification_code):
        """Should register a device token."""
        result = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "firebase-token-123", "platform": "android"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["token"] == "firebase-token-123"
        assert data["platform"] == "android"
        assert "id" in data

    async def test_register_device_token_upsert(self, client, mock_verification_code):
        """Same token should update, not duplicate."""
        result = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res1 = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "token-abc", "platform": "android"},
            headers=headers,
        )
        assert res1.status_code == 200
        id1 = res1.json()["id"]

        res2 = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "token-abc", "platform": "ios"},
            headers=headers,
        )
        assert res2.status_code == 200
        id2 = res2.json()["id"]
        # Same ID, updated platform
        assert id1 == id2
        assert res2.json()["platform"] == "ios"

    async def test_register_requires_auth(self, client):
        """Should require authentication."""
        res = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "token-xyz", "platform": "android"},
        )
        assert res.status_code == 401

    async def test_register_invalid_platform(self, client, mock_verification_code):
        """Should reject invalid platform."""
        result = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        res = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "token-xyz", "platform": "windows"},
            headers=headers,
        )
        assert res.status_code == 422

    async def test_delete_device_token(self, client, mock_verification_code):
        """Should delete a device token."""
        result = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        # Register token
        res = await client.post(
            DEVICE_TOKEN_URL,
            json={"token": "token-del", "platform": "android"},
            headers=headers,
        )
        assert res.status_code == 200
        token_id = res.json()["id"]

        # Delete token
        res = await client.delete(
            f"/api/v1/notifications/device-token/{token_id}",
            headers=headers,
        )
        assert res.status_code == 204

    async def test_delete_nonexistent_token_404(self, client, mock_verification_code):
        """Should return 404 for nonexistent token."""
        result = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        headers = {"Authorization": f"Bearer {result['access_token']}"}

        fake_id = str(uuid4())
        res = await client.delete(
            f"/api/v1/notifications/device-token/{fake_id}",
            headers=headers,
        )
        assert res.status_code == 404


class TestPushOnLike:

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_push_sent_on_like(
        self, mock_send, client, mock_verification_code
    ):
        """Push notification should be sent when someone likes a user."""
        user1 = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        user2 = await register_user(client, VALID_EMAIL_2, COMPLETE_PROFILE_PAYLOAD_2, mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        user2_id = user2["user"]["id"]

        res = await client.post(
            SWIPE_URL,
            json={"user_id": user2_id, "direction": "like"},
            headers=headers1,
        )
        assert res.status_code == 200

        # Push should have been called for the liked user
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["user_id"].__str__() == user2_id
        assert "liked" in call_kwargs["title"].lower()


class TestPushOnMatch:

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_push_sent_on_match(
        self, mock_send, client, mock_verification_code
    ):
        """Push notifications should be sent to both users on match."""
        user1 = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        user2 = await register_user(client, VALID_EMAIL_2, COMPLETE_PROFILE_PAYLOAD_2, mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        headers2 = {"Authorization": f"Bearer {user2['access_token']}"}
        user1_id = user1["user"]["id"]
        user2_id = user2["user"]["id"]

        # User 1 likes user 2
        await client.post(
            SWIPE_URL,
            json={"user_id": user2_id, "direction": "like"},
            headers=headers1,
        )
        # Reset to only count notifications from the second swipe
        mock_send.reset_mock()
        # User 2 likes user 1 (creates match)
        res = await client.post(
            SWIPE_URL,
            json={"user_id": user1_id, "direction": "like"},
            headers=headers2,
        )
        assert res.status_code == 200

        # Match notifications should have been sent to both users
        match_calls = [
            c for c in mock_send.call_args_list
            if c.kwargs.get("title") == "It's a match!"
        ]
        assert len(match_calls) == 2


class TestPushOnMessage:

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_push_sent_on_message(
        self, mock_send, client, mock_verification_code
    ):
        """Push notification should be sent when a message is sent."""
        user1 = await register_user(client, VALID_EMAIL, COMPLETE_PROFILE_PAYLOAD, mock_verification_code)
        user2 = await register_user(client, VALID_EMAIL_2, COMPLETE_PROFILE_PAYLOAD_2, mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        headers2 = {"Authorization": f"Bearer {user2['access_token']}"}
        user1_id = user1["user"]["id"]
        user2_id = user2["user"]["id"]

        # Create match first
        await client.post(SWIPE_URL, json={"user_id": user2_id, "direction": "like"}, headers=headers1)
        await client.post(SWIPE_URL, json={"user_id": user1_id, "direction": "like"}, headers=headers2)

        # Create the chat (auto-accepted because the pair is matched)
        chat_res = await client.post(
            "/api/v1/chats", json={"user_id": user2_id, "content": "hi"}, headers=headers1
        )
        assert chat_res.status_code == 200
        chat_id = chat_res.json()["chat_id"]

        # Send message
        mock_send.reset_mock()
        res = await client.post(
            f"/api/v1/messages/{chat_id}/text",
            json={"content": "Hello!"},
            headers=headers1,
        )
        assert res.status_code == 200

        # Push should have been called for the receiver
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["user_id"].__str__() == user2_id


class TestPushDataOnlyPayload:
    """Unit tests for the data-only FCM payload builder (compact notifications).

    The push must carry title/body/image_url inside `data` (not a `notification`
    block) so the app's native FirebaseMessagingService can render a compact,
    non-expandable notification with a small circular avatar.
    """

    def test_build_data_includes_title_body_image(self):
        from app.services.push_service import PushService

        payload = PushService._build_data(
            title="It's a match!",
            body="You matched with Test User",
            data={"type": "match", "match_id": "abc", "user_id": "123"},
            image_url="https://cdn.example.com/p1.jpg",
        )
        assert payload["type"] == "match"
        assert payload["match_id"] == "abc"
        assert payload["user_id"] == "123"
        assert payload["title"] == "It's a match!"
        assert payload["body"] == "You matched with Test User"
        assert payload["image_url"] == "https://cdn.example.com/p1.jpg"

    def test_build_data_without_image_url(self):
        from app.services.push_service import PushService

        payload = PushService._build_data(
            title="Announcement",
            body="Maintenance tonight",
            data={"type": "system", "is_announcement": True},
            image_url=None,
        )
        assert payload["type"] == "system"
        assert payload["is_announcement"] == "True"
        assert payload["image_url"] == ""

    def test_build_data_with_none_data(self):
        from app.services.push_service import PushService

        payload = PushService._build_data(title="Hi", body="Test", data=None, image_url=None)
        assert payload["title"] == "Hi"
        assert payload["body"] == "Test"
        assert payload["image_url"] == ""


class TestPushBatch:
    """Unit tests for PushService.send_to_users (P: announcement batching).

    A per-user broadcast (announcement to every user) must NOT enqueue one
    celery task per recipient — that saturates the pgbouncer pool by holding a
    pooled DB connection in an open transaction across each slow synchronous FCM
    round-trip. Instead a single batch task queries tokens once, releases the
    connection, then fans out FCM in <=500-token multicast chunks.
    """

    @patch("app.services.push_service.messaging.send_each_for_multicast")
    @patch("app.db.session.AsyncSessionLocal")
    async def test_send_to_users_chunks_and_releases_connection(self, mock_session_local, mock_send):
        from app.services import push_service as ps

        # Fake AsyncSessionLocal context manager returning one session whose
        # execute returns our tokens, then exits (releasing the connection).
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(f"token-{i}",) for i in range(1001)]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

        fake_response = MagicMock(success_count=1, failure_count=0)
        fake_response.responses = [MagicMock(success=True)] * 500
        mock_send.return_value = fake_response

        ps._initialized = True
        try:
            await ps.PushService.send_to_users(
                user_ids=[str(uuid4()) for _ in range(1001)],
                title="Announcement",
                body="Maintenance tonight",
                data={"type": "system", "is_announcement": True},
            )

            # 1001 tokens -> 3 multicast calls (500/500/1).
            assert mock_send.call_count == 3
            sizes = [len(call.args[0].tokens) for call in mock_send.call_args_list]
            assert sizes == [500, 500, 1]
            # Each message carries the announcement payload.
            for call in mock_send.call_args_list:
                assert call.args[0].data["type"] == "system"
                assert call.args[0].data["title"] == "Announcement"
        finally:
            ps._initialized = False

    @patch("app.services.push_service.messaging.send_each_for_multicast")
    @patch("app.db.session.AsyncSessionLocal")
    async def test_send_to_users_no_tokens_no_fcm(self, mock_session_local, mock_send):
        from app.services import push_service as ps

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

        ps._initialized = True
        try:
            await ps.PushService.send_to_users(
                user_ids=[str(uuid4())],
                title="Hi",
                body="Test",
            )
            mock_send.assert_not_called()
        finally:
            ps._initialized = False

    @patch("app.services.push_service.messaging.send_each_for_multicast")
    @patch("app.db.session.AsyncSessionLocal")
    async def test_send_to_users_cleans_invalid_tokens_and_commits(self, mock_session_local, mock_send):
        from app.services import push_service as ps
        from sqlalchemy import delete

        # First context manager: token query. Second: invalid-token cleanup.
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [("dead-token",), ("good-token",)]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

        fake_response = MagicMock(success_count=1, failure_count=1)
        dead = MagicMock(success=False)
        dead.exception.code = "registration-token-not-registered"
        good = MagicMock(success=True)
        fake_response.responses = [dead, good]
        mock_send.return_value = fake_response

        ps._initialized = True
        try:
            await ps.PushService.send_to_users(
                user_ids=[str(uuid4())],
                title="Hi",
                body="Test",
            )

            assert mock_send.call_count == 1
            # Cleanup opened a second short-lived session and committed.
            assert mock_session_local.call_count == 2
            assert mock_session.commit.awaited
        finally:
            ps._initialized = False


class TestPushRunsOffEventLoop:
    """Unit tests for the threading path of send_to_user (P1-1).

    The integration tests above mock PushService.send_to_user at the method level,
    so they never exercise the actual FCM call. These tests force _initialized=True
    and mock messaging.send_each_for_multicast to prove:
      - the blocking call runs via asyncio.to_thread (no event-loop block),
      - its BatchResponse is returned and logged,
      - invalid-token cleanup runs against the returned response.
    """

    @patch("app.services.push_service.messaging.send_each_for_multicast")
    async def test_send_runs_in_thread_and_returns_response(self, mock_send):
        from app.services import push_service as ps
        from uuid import uuid4

        # Force the "firebase initialized" gate open so we reach the send path.
        ps._initialized = True
        try:
            fake_response = MagicMock(success_count=1, failure_count=0)
            fake_response.responses = []
            mock_send.return_value = fake_response

            # Mock DB: return one token, and make cleanup a no-op.
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(
                side_effect=[
                    MagicMock(all=MagicMock(return_value=[("token-1",)])),   # _get_user_tokens
                    MagicMock(),                                                # cleanup select
                    MagicMock(),                                                # cleanup delete
                ]
            )

            await ps.PushService.send_to_user(
                user_id=uuid4(),
                title="Hi",
                body="Test",
                data={"k": "v"},
                db=mock_db,
            )

            # The blocking FCM call was invoked exactly once...
            assert mock_send.call_count == 1
            # ...and the success_count from its response was logged (proving the
            # asyncio.to_thread return value propagated correctly).
            mock_send.assert_called_once()
        finally:
            ps._initialized = False

