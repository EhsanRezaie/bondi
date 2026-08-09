# tests/done/test_websocket.py
import pytest
import json
import base64
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
from uuid import UUID

from app.models.user import User
from app.services.websocket_manager import WebSocketManager

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
MESSAGES_URL = "/api/v1/messages"
CHATS_URL = "/api/v1/chats"
VALID_CODE = "123456"
VALID_PASSWORD = "strongpass123"

MALE_PAYLOAD = {
    "name": "WS Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 180,
    "weight": 75,
}

FEMALE_PAYLOAD = {
    "name": "WS Female",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 165,
    "weight": 60,
}


async def register_user_full(client, email, payload, mock_vcode):
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200
    await mock_vcode(email, VALID_CODE)
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email, "code": VALID_CODE, "password": VALID_PASSWORD,
    })
    assert res.status_code == 200
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
    assert res.status_code == 200
    return res.json()


async def register_get_headers(client, email, payload, mock_vcode):
    result = await register_user_full(client, email, payload, mock_vcode)
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    return headers, result["user"]["id"]


def create_jpeg():
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


def create_mp3():
    return base64.b64decode(
        b"SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAABIAAAADAAAVFRSU0UAAAAP"
        b"AAAAQVVESU8gQ09ERVgAAABMYXZmNTguNzYuMTAwAAAAAAAAAAAAAAD/"
    )


# =============================================================================
# new_match push shape (via mock_websocket_manager)
# =============================================================================

class TestWebSocketMatchPush:
    """Verify the shape of new_match WebSocket push data."""

    async def test_new_match_push_shape(
        self, client, mock_websocket_manager, mock_verification_code, db_session
    ):
        """broadcast_match should receive correctly shaped user data dicts."""
        male_headers, male_id = await register_get_headers(
            client, "wsmatch_m@example.com", MALE_PAYLOAD, mock_verification_code
        )
        female_headers, female_id = await register_get_headers(
            client, "wsmatch_f@example.com", FEMALE_PAYLOAD, mock_verification_code
        )

        result = await db_session.execute(
            select(User).options(selectinload(User.profile)).where(
                User.id.in_([male_id, female_id])
            )
        )
        result.scalars().all()

        # Create mutual match -> triggers broadcast_match in background task
        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)

        # Let background tasks finish
        import asyncio
        await asyncio.sleep(0.1)

        assert mock_websocket_manager.broadcast_match.called, "broadcast_match was not called"

        call_args = mock_websocket_manager.broadcast_match.call_args
        args, kwargs = call_args
        # (user1_id, user2_id, match_id, user1_data, user2_data, redis)
        assert len(args) == 6
        user1_id, user2_id, match_id, user1_data, user2_data = args[:5]

        # Verify user1_data shape
        for key in ("id", "name", "age", "main_photo_url"):
            assert key in user1_data, f"user1_data missing '{key}'"
        assert isinstance(user1_data["id"], str)
        assert isinstance(user1_data["name"], str)
        assert isinstance(user1_data["age"], int)
        # main_photo_url can be None (no photo uploaded)

        # Verify user2_data shape
        for key in ("id", "name", "age", "main_photo_url"):
            assert key in user2_data, f"user2_data missing '{key}'"

        # user1_id = current_user of second swipe (female), user2_id = target (male)
        assert user1_id == str(female_id)
        assert user2_id == str(male_id)
        assert match_id is not None


class TestWebSocketMessagePush:
    """Verify the shape of new_message WebSocket push data (text/photo/voice)."""

    async def test_text_message_push_shape(
        self, client, mock_websocket_manager, mock_verification_code, db_session
    ):
        """Text message send_to_match should receive correctly shaped data."""
        male_headers, male_id = await register_get_headers(
            client, "wstext_m@example.com", MALE_PAYLOAD, mock_verification_code
        )
        female_headers, female_id = await register_get_headers(
            client, "wstext_f@example.com", FEMALE_PAYLOAD, mock_verification_code
        )

        result = await db_session.execute(
            select(User).options(selectinload(User.profile)).where(
                User.id.in_([male_id, female_id])
            )
        )
        result.scalars().all()

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)
        chat_res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = chat_res.json()["chat_id"]

        import asyncio
        await asyncio.sleep(0.1)

        # Patch messages module's send_to_match
        with patch("app.api.v1.endpoints.messages.websocket_manager.send_to_conversation", new_callable=AsyncMock) as mock_send:
            await client.post(
                f"{MESSAGES_URL}/{chat_id}/text",
                json={"content": "Hello WS"},
                headers=male_headers,
            )
            await asyncio.sleep(0.1)

            assert mock_send.called, "send_to_match was not called"
            call_args = mock_send.call_args
            args, kwargs = call_args
            # (match_id_str, sender_id_str, message_data, other_user_id=...)
            match_id_str, sender_id_str, message_data = args[0], args[1], args[2]

            # Top-level envelope
            assert message_data["type"] == "new_message"

            data = message_data["data"]
            assert data["message_type"] == "text"
            assert "id" in data and isinstance(data["id"], str)
            assert data["content"] == "Hello WS"
            assert data["sender_id"] == str(male_id)
            assert "sent_at" in data and isinstance(data["sent_at"], str)
            # Text should NOT have media_url or duration
            assert "media_url" not in data
            assert "duration" not in data

    async def test_photo_message_push_shape(
        self, client, mock_websocket_manager, mock_verification_code, db_session
    ):
        """Photo message send_to_match should receive correctly shaped data."""
        male_headers, male_id = await register_get_headers(
            client, "wsphoto_m@example.com", MALE_PAYLOAD, mock_verification_code
        )
        female_headers, female_id = await register_get_headers(
            client, "wsphoto_f@example.com", FEMALE_PAYLOAD, mock_verification_code
        )

        result = await db_session.execute(
            select(User).options(selectinload(User.profile)).where(
                User.id.in_([male_id, female_id])
            )
        )
        result.scalars().all()

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)
        chat_res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = chat_res.json()["chat_id"]

        import asyncio
        await asyncio.sleep(0.1)

        with patch("app.api.v1.endpoints.messages.websocket_manager.send_to_conversation", new_callable=AsyncMock) as mock_send:
            files = {"file": ("test.jpg", create_jpeg(), "image/jpeg")}
            data = {"caption": "Photo caption"}
            await client.post(
                f"{MESSAGES_URL}/{chat_id}/photo",
                files=files,
                data=data,
                headers=male_headers,
            )
            await asyncio.sleep(0.1)

            assert mock_send.called
            call_args = mock_send.call_args
            args = call_args[0]
            message_data = args[2]

            assert message_data["type"] == "new_message"
            d = message_data["data"]
            assert d["message_type"] == "photo"
            assert "id" in d and isinstance(d["id"], str)
            assert "media_url" in d and isinstance(d["media_url"], str)
            assert d["caption"] == "Photo caption"
            assert d["sender_id"] == str(male_id)
            assert "sent_at" in d and isinstance(d["sent_at"], str)

    async def test_voice_message_push_shape(
        self, client, mock_websocket_manager, mock_verification_code, db_session
    ):
        """Voice message send_to_match should receive correctly shaped data."""
        male_headers, male_id = await register_get_headers(
            client, "wsvoice_m@example.com", MALE_PAYLOAD, mock_verification_code
        )
        female_headers, female_id = await register_get_headers(
            client, "wsvoice_f@example.com", FEMALE_PAYLOAD, mock_verification_code
        )

        result = await db_session.execute(
            select(User).options(selectinload(User.profile)).where(
                User.id.in_([male_id, female_id])
            )
        )
        result.scalars().all()

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)
        chat_res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        chat_id = chat_res.json()["chat_id"]

        import asyncio
        await asyncio.sleep(0.1)

        with patch("app.api.v1.endpoints.messages.websocket_manager.send_to_conversation", new_callable=AsyncMock) as mock_send:
            files = {"file": ("test.mp3", create_mp3(), "audio/mpeg")}
            data = {"duration": 30}
            await client.post(
                f"{MESSAGES_URL}/{chat_id}/voice",
                files=files,
                data=data,
                headers=male_headers,
            )
            await asyncio.sleep(0.1)

            assert mock_send.called
            call_args = mock_send.call_args
            args = call_args[0]
            message_data = args[2]

            assert message_data["type"] == "new_message"
            d = message_data["data"]
            assert d["message_type"] == "voice"
            assert "id" in d and isinstance(d["id"], str)
            assert "media_url" in d and isinstance(d["media_url"], str)
            assert "duration" in d and isinstance(d["duration"], int)
            assert d["sender_id"] == str(male_id)
            assert "sent_at" in d and isinstance(d["sent_at"], str)
            assert "caption" not in d
            assert "content" not in d


# =============================================================================
# WebSocketManager unit tests — verify internal envelope construction
# =============================================================================

class TestWebSocketManagerUnit:
    """Direct unit tests of WebSocketManager to verify JSON envelope shapes."""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self.manager = WebSocketManager()
        self.manager.active_connections = {}
        self.manager.chat_connections = {}

    async def test_broadcast_match_envelope(self):
        """broadcast_match should publish correct new_match JSON to Redis."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()

        user1_id = "u1"
        user2_id = "u2"
        match_id = "m1"
        user1_data = {"id": "u1", "name": "Alice", "age": 25, "main_photo_url": None}
        user2_data = {"id": "u2", "name": "Bob", "age": 30, "main_photo_url": "http://photo.url"}

        await self.manager.broadcast_match(
            user1_id, user2_id, match_id, user1_data, user2_data, mock_redis,
        )

        assert mock_redis.publish.call_count == 2

        # First publish -> user1 gets user2's data
        call1 = mock_redis.publish.call_args_list[0]
        channel1, raw1 = call1[0]
        payload1 = json.loads(raw1)
        assert payload1["type"] == "new_match"
        assert payload1["data"]["match_id"] == match_id
        assert payload1["data"]["user"]["id"] == "u2"
        assert payload1["data"]["user"]["name"] == "Bob"
        assert payload1["data"]["user"]["age"] == 30
        assert payload1["data"]["user"]["main_photo_url"] == "http://photo.url"

        # Second publish -> user2 gets user1's data
        call2 = mock_redis.publish.call_args_list[1]
        channel2, raw2 = call2[0]
        payload2 = json.loads(raw2)
        assert payload2["type"] == "new_match"
        assert payload2["data"]["match_id"] == match_id
        assert payload2["data"]["user"]["id"] == "u1"
        assert payload2["data"]["user"]["name"] == "Alice"
        assert payload2["data"]["user"]["age"] == 25
        assert payload2["data"]["user"]["main_photo_url"] is None

    async def test_send_to_match_envelope(self):
        """send_to_match should publish correct new_message JSON to Redis."""
        mock_redis = AsyncMock()

        match_id = "m1"
        sender_id = "sender1"
        other_user_id = "receiver1"

        message_data = {
            "type": "new_message",
            "data": {
                "id": "msg1",
                "message_type": "text",
                "content": "Hello",
                "sender_id": sender_id,
                "sent_at": "2026-06-28T12:00:00",
            }
        }

        await self.manager.send_to_match(
            match_id, sender_id, message_data, other_user_id, mock_redis,
        )

        assert mock_redis.publish.call_count == 2

        for call_args in mock_redis.publish.call_args_list:
            channel, raw = call_args[0]
            payload = json.loads(raw)
            assert payload["type"] == "new_message"
            assert payload["data"]["id"] == "msg1"
            assert payload["data"]["message_type"] == "text"
            assert payload["data"]["content"] == "Hello"

    async def test_send_personal_message_envelope(self):
        """send_personal_message publishes correct JSON to Redis."""
        mock_redis = AsyncMock()

        user_id = "u1"
        message = {"type": "test_event", "data": {"key": "value"}}
        await self.manager.send_personal_message(user_id, message, mock_redis)

        mock_redis.publish.assert_called_once()
        call = mock_redis.publish.call_args
        channel = call[0][0]
        raw = call[0][1]
        assert channel == f"ws:user:{user_id}"
        sent = json.loads(raw)
        assert sent["type"] == "test_event"
        assert sent["data"]["key"] == "value"

    async def test_disconnect_cleans_up(self):
        """Disconnect should remove websocket from active_connections."""
        mock_ws = MagicMock()
        user_id = "u1"
        self.manager.active_connections[user_id] = {mock_ws}

        mock_redis = AsyncMock()
        await self.manager.disconnect(mock_ws, user_id, mock_redis)
        assert user_id not in self.manager.active_connections

    async def test_send_personal_message_no_connection(self):
        """Should not raise when user has no active connection."""
        mock_redis = AsyncMock()
        await self.manager.send_personal_message("nonexistent", {"type": "test"}, mock_redis)

    async def test_set_typing(self):
        """set_typing publishes typing event and sets Redis key."""
        mock_redis = AsyncMock()
        match_id = "m1"
        user_id = "u1"

        await self.manager.set_typing(match_id, user_id, mock_redis)

        assert mock_redis.setex.call_count == 1
        assert mock_redis.publish.call_count == 1
        channel, raw = mock_redis.publish.call_args[0]
        payload = json.loads(raw)
        assert payload["type"] == "typing"
        assert payload["chat_id"] == match_id
        assert payload["user_id"] == user_id

    async def test_clear_typing(self):
        """clear_typing deletes Redis key and publishes typing_stopped."""
        mock_redis = AsyncMock()
        match_id = "m1"
        user_id = "u1"

        await self.manager.clear_typing(match_id, user_id, mock_redis)

        assert mock_redis.delete.call_count == 1
        assert mock_redis.publish.call_count == 1
        channel, raw = mock_redis.publish.call_args[0]
        payload = json.loads(raw)
        assert payload["type"] == "typing_stopped"
        assert payload["chat_id"] == match_id
        assert payload["user_id"] == user_id

    async def test_is_typing(self):
        """is_typing checks Redis key existence."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1

        result = await self.manager.is_typing("m1", "u1", mock_redis)
        assert result is True
        mock_redis.exists.assert_called_once_with("typing:m1:u1")

    async def test_is_online(self):
        """is_online checks Redis key existence."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1

        result = await self.manager.is_online("u1", mock_redis)
        assert result is True
        mock_redis.exists.assert_called_once_with("online:u1")

    async def test_heartbeat(self):
        """heartbeat refreshes online TTL."""
        mock_redis = AsyncMock()
        await self.manager.heartbeat("u1", mock_redis)
        mock_redis.setex.assert_called_once_with("online:u1", 60, "1")

    async def test_get_online_status_bulk(self):
        """get_online_status_bulk returns correct map."""
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, 0, 1])
        mock_redis.pipeline.return_value = mock_pipe

        result = await self.manager.get_online_status_bulk(
            ["u1", "u2", "u3"], mock_redis,
        )
        assert result == {"u1": True, "u2": False, "u3": True}
        mock_redis.pipeline.assert_called_once()
        mock_pipe.execute.assert_called_once()


class TestChatChannel:
    """Conversation channel resolution + topic-gated delivery for chats."""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        self.manager = WebSocketManager()
        self.manager.active_connections = {}
        self.manager.user_subscriptions = {}

    def test_channel_is_chat_id_based(self):
        """conversation_channel always resolves to ws:chat:{chat_id}."""
        channel = self.manager.conversation_channel("chat-1", "u1", "u2")
        assert channel == "ws:chat:chat-1"

    async def test_send_to_conversation_publishes_pair_channel(self):
        """send_to_conversation publishes sender + receiver frames on one channel."""
        mock_redis = AsyncMock()
        channel = self.manager.conversation_channel("chat-1")
        message = {"type": "new_message", "data": {"id": "m1", "content": "hi"}}

        await self.manager.send_to_conversation(channel, "u1", message, "u2", mock_redis)

        assert mock_redis.publish.call_count == 2
        published_channels = [call[0][0] for call in mock_redis.publish.call_args_list]
        assert all(c == channel for c in published_channels)
        payloads = [json.loads(call[0][1]) for call in mock_redis.publish.call_args_list]
        targets = {p["_target_user"] for p in payloads}
        assert targets == {"u1", "u2"}

    async def test_subscribe_tracks_topic_and_sets_online(self):
        """subscribe registers the (chat_id, peer) topic and marks user online."""
        mock_redis = AsyncMock()
        await self.manager.subscribe("u1", "chat-1", "u2", mock_redis)
        assert self.manager.user_subscriptions["u1"]["chat-1"] == "u2"
        assert mock_redis.setex.called

    async def test_unsubscribe_removes_topic(self):
        """unsubscribe drops the topic and cleans the empty per-user map."""
        mock_redis = AsyncMock()
        await self.manager.subscribe("u1", "chat-1", "u2", mock_redis)
        await self.manager.unsubscribe("u1", "chat-1")
        assert "u1" not in self.manager.user_subscriptions

    async def test_deliver_only_to_subscribed_target(self):
        """Deliver chat event only to the subscribed user matching _target_user."""
        mock_redis = AsyncMock()
        ws_target = AsyncMock()
        ws_other = AsyncMock()
        self.manager.active_connections = {"u2": {ws_target}, "u3": {ws_other}}
        await self.manager.subscribe("u2", "chat-1", "u1", mock_redis)
        await self.manager.subscribe("u3", "chat-1", "u1", mock_redis)

        channel = self.manager.conversation_channel("chat-1")
        await self.manager._deliver_to_chat_local(
            channel, {"type": "new_message", "_target_user": "u2", "data": {}}, "u2"
        )

        ws_target.send_text.assert_awaited()
        ws_other.send_text.assert_not_awaited()

    async def test_deliver_prunes_dead_sockets(self):
        """A failing socket is dropped instead of crashing delivery."""
        mock_redis = AsyncMock()
        dead_ws = AsyncMock()
        dead_ws.send_text = AsyncMock(side_effect=Exception("closed"))
        good_ws = AsyncMock()
        self.manager.active_connections = {"u2": {dead_ws, good_ws}}
        await self.manager.subscribe("u2", "chat-1", "u1", mock_redis)

        channel = self.manager.conversation_channel("chat-1")
        await self.manager._deliver_to_chat_local(
            channel, {"type": "new_message", "data": {}}, None
        )

        good_ws.send_text.assert_awaited()
        assert dead_ws not in self.manager.active_connections["u2"]

    async def test_presence_broadcast_reaches_peer_open_chats(self):
        """connect-time presence is routed to peers with an open chat."""
        mock_redis = AsyncMock()
        ws_peer = AsyncMock()
        self.manager.active_connections = {"u2": {ws_peer}}
        await self.manager.subscribe("u2", "chat-1", "u1", mock_redis)

        await self.manager.broadcast_peer_presence(
            "u1", {"type": "user_online", "user_id": "u1"}, mock_redis
        )

        assert mock_redis.publish.called


# =============================================================================
# WS token validation — validate_ws_token (deps) must unwrap sub correctly
# =============================================================================
class TestWsTokenValidation:
    async def test_validate_ws_token_returns_user_id(self, patch_redis):
        """A valid access token must yield the subject string, not raise."""
        from app.core.security import create_access_token
        from app.core.deps import validate_ws_token

        user_id = "e9dfcd4a-3cf4-42d2-84ef-4a45cd7473ff"
        token = create_access_token(user_id, token_version=1)

        result = await validate_ws_token(token, None)
        assert result == user_id

    @pytest.mark.parametrize("bad_token", ["not-a-jwt", "", "a.b.c"])
    async def test_validate_ws_token_rejects_invalid(self, patch_redis, bad_token):
        import pytest as _pytest
        from app.core.deps import validate_ws_token

        with _pytest.raises(ValueError):
            await validate_ws_token(bad_token, None)


class TestWsInboundValidation:
    """P3-1: malformed/garbage WS frames must be rejected before dispatch."""

    def test_ping_valid(self):
        from app.schemas.message import WsInbound
        msg = WsInbound.model_validate({"type": "ping"})
        assert msg.type == "ping"

    def test_subscribe_requires_chat_id(self):
        from pydantic import ValidationError
        from app.schemas.message import WsInbound
        with pytest.raises(ValidationError):
            WsInbound.model_validate({"type": "subscribe"})

    def test_subscribe_requires_valid_uuid(self):
        from pydantic import ValidationError
        from app.schemas.message import WsInbound
        with pytest.raises(ValidationError):
            WsInbound.model_validate({"type": "subscribe", "chat_id": "not-a-uuid"})

    def test_unknown_type_rejected(self):
        from pydantic import ValidationError
        from app.schemas.message import WsInbound
        with pytest.raises(ValidationError):
            WsInbound.model_validate({"type": "exploit"})

    def test_read_caps_message_ids(self):
        from pydantic import ValidationError
        from app.schemas.message import WsInbound
        big = {"type": "read", "message_ids": [str(UUID(int=i)) for i in range(201)]}
        with pytest.raises(ValidationError):
            WsInbound.model_validate(big)
        ok = {"type": "read", "message_ids": [str(UUID(int=i)) for i in range(200)]}
        assert len(WsInbound.model_validate(ok).message_ids) == 200

    def test_read_rejects_bad_message_id(self):
        from pydantic import ValidationError
        from app.schemas.message import WsInbound
        with pytest.raises(ValidationError):
            WsInbound.model_validate({"type": "read", "message_ids": ["garbage"]})

    def test_read_empty_ids_ok(self):
        from app.schemas.message import WsInbound
        msg = WsInbound.model_validate({"type": "read", "message_ids": []})
        assert msg.message_ids == []
