import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.chat import Chat
from app.models.user import User
from app.models.user_settings import UserSettings


def _user():
    return User(
        id=uuid4(),
        phone=f"+9891{uuid4().hex[:10]}",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )


class _Socket:
    def __init__(self, frames=()):
        self.frames = iter(frames)
        self.sent = []
        self.closed = []

    async def receive_text(self):
        try:
            return next(self.frames)
        except StopIteration:
            raise TimeoutError from None

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code, reason):
        self.closed.append((code, reason))


def _manager():
    manager = MagicMock()
    manager.connect = AsyncMock()
    manager.disconnect = AsyncMock()
    manager.heartbeat = AsyncMock()
    manager.subscribe = AsyncMock()
    manager.unsubscribe = AsyncMock()
    manager.set_typing = AsyncMock()
    manager.clear_typing = AsyncMock()
    manager.is_online = AsyncMock(return_value=True)
    manager.send_to_conversation = AsyncMock()
    manager.broadcast_peer_presence = AsyncMock()
    manager.conversation_channel.side_effect = lambda chat_id: f"ws:chat:{chat_id}"
    return manager


async def _add_pair(db_session, *, hide_last_seen=False):
    user, peer = _user(), _user()
    user.last_seen_at = datetime.now(UTC)
    peer.last_seen_at = datetime(2026, 1, 2, tzinfo=UTC)
    chat = Chat(
        id=uuid4(), initiator_id=user.id, recipient_id=peer.id, status="accepted"
    )
    db_session.add_all(
        [
            user,
            peer,
            chat,
            UserSettings(user_id=user.id, hide_last_seen=False, hide_online_status=False),
            UserSettings(
                user_id=peer.id,
                hide_last_seen=hide_last_seen,
                hide_online_status=False,
            ),
        ]
    )
    await db_session.commit()
    return user, peer, chat


class TestWebsocketStreamEndpoint:
    async def test_auth_failure_closes_with_4401(self, db_session, monkeypatch):
        from app.api.v1.websocket import stream

        socket = _Socket()
        validate = AsyncMock(side_effect=RuntimeError("invalid token"))
        monkeypatch.setattr(stream, "validate_ws_token", validate)
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)

        await stream.stream_websocket(socket, token="not-a-real-token", redis=MagicMock(), db=db_session)

        assert socket.closed == [(4401, "Unauthorized")]
        manager.connect.assert_not_awaited()

    async def test_bad_json_ping_and_unknown_type(self, db_session, monkeypatch):
        from app.api.v1.websocket import stream

        user, _, _ = await _add_pair(db_session)
        socket = _Socket(["not-json", json.dumps({"type": "ping"}), json.dumps({"type": "unknown"})])
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(user.id)))
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        await stream.stream_websocket(socket, token="opaque-test-token", redis=redis, db=db_session)

        assert socket.sent[:2] == [
            {"type": "error", "reason": "bad_message"},
            {"type": "pong"},
        ]
        assert socket.sent[2] == {"type": "error", "reason": "bad_message"}
        manager.heartbeat.assert_awaited_once_with(str(user.id), redis)
        manager.disconnect.assert_awaited_once_with(socket, str(user.id), redis)
        manager.broadcast_peer_presence.assert_awaited()

    async def test_subscribe_typing_read_unsubscribe_and_presence_snapshot(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream
        from app.schemas.message import WS_MAX_READ_IDS

        user, peer, chat = await _add_pair(db_session)
        payloads = [
            {"type": "subscribe", "chat_id": str(chat.id)},
            {"type": "typing", "chat_id": str(chat.id)},
            {"type": "typing_stopped", "chat_id": str(chat.id)},
            {"type": "read", "message_ids": [str(uuid4()) for _ in range(WS_MAX_READ_IDS)]},
            {"type": "unsubscribe", "chat_id": str(chat.id)},
        ]
        socket = _Socket([json.dumps(item) for item in payloads])
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(user.id)))
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        await stream.stream_websocket(socket, token="opaque-test-token", redis=redis, db=db_session)

        assert socket.sent[0] == {
            "type": "user_online",
            "chat_id": str(chat.id),
            "user_id": str(peer.id),
        }
        manager.subscribe.assert_awaited_once_with(
            str(user.id), str(chat.id), str(peer.id), redis
        )
        manager.set_typing.assert_awaited_once_with(f"ws:chat:{chat.id}", str(user.id), redis)
        manager.clear_typing.assert_awaited_once_with(f"ws:chat:{chat.id}", str(user.id), redis)
        read_event = manager.send_to_conversation.await_args.kwargs["message"]
        assert read_event["type"] == "messages_read"
        assert read_event["reader_id"] == str(user.id)
        assert len(read_event["message_ids"]) == WS_MAX_READ_IDS
        manager.unsubscribe.assert_awaited_once_with(str(user.id), str(chat.id))
        redis.publish.assert_awaited()

    async def test_non_member_subscribe_does_not_send_presence(self, db_session, monkeypatch):
        from app.api.v1.websocket import stream

        user, peer, chat = await _add_pair(db_session)
        outsider = _user()
        db_session.add(outsider)
        await db_session.commit()
        socket = _Socket([json.dumps({"type": "subscribe", "chat_id": str(chat.id)})])
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(outsider.id)))
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        await stream.stream_websocket(socket, token="opaque-test-token", redis=redis, db=db_session)

        assert socket.sent == []
        manager.subscribe.assert_not_awaited()

    async def test_hidden_last_seen_is_omitted_from_offline_snapshot(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream

        user, peer, chat = await _add_pair(db_session, hide_last_seen=True)
        socket = _Socket([json.dumps({"type": "subscribe", "chat_id": str(chat.id)})])
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(user.id)))
        manager = _manager()
        manager.is_online = AsyncMock(return_value=False)
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        await stream.stream_websocket(socket, token="opaque-test-token", redis=redis, db=db_session)

        assert socket.sent[0] == {
            "type": "user_offline",
            "chat_id": str(chat.id),
            "user_id": str(peer.id),
            "last_seen_at": None,
        }

    async def test_resolve_chat_rejects_invalid_and_blocked_chat(self, db_session):
        from app.api.v1.websocket.stream import _resolve_chat

        user, peer, chat = await _add_pair(db_session)
        assert await _resolve_chat(db_session, "not-a-uuid", str(user.id)) is None
        assert await _resolve_chat(db_session, str(chat.id), str(uuid4())) is None

        from app.models.block import Block

        db_session.add(Block(blocker_id=peer.id, blocked_id=user.id))
        await db_session.commit()
        assert await _resolve_chat(db_session, str(chat.id), str(user.id)) is None

    async def test_presence_snapshot_online_and_visible_last_seen(self, db_session, monkeypatch):
        from app.api.v1.websocket import stream

        _, peer, chat = await _add_pair(db_session)
        manager = _manager()
        manager.is_online.return_value = True
        monkeypatch.setattr(stream, "websocket_manager", manager)
        online = await stream._presence_snapshot(db_session, str(peer.id), MagicMock(), str(chat.id))
        assert online == {
            "type": "user_online",
            "chat_id": str(chat.id),
            "user_id": str(peer.id),
        }

        manager.is_online.return_value = False
        offline = await stream._presence_snapshot(db_session, str(peer.id), MagicMock(), str(chat.id))
        assert offline["type"] == "user_offline"
        assert offline["last_seen_at"] == peer.last_seen_at.isoformat()

    async def test_typing_frame_resolves_chat_and_uses_canonical_channel(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream

        user, peer, chat = await _add_pair(db_session)
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()

        await stream._relay_typing(
            db_session, str(user.id), chat.id, None, "typing", redis
        )
        manager.set_typing.assert_awaited_once_with(f"ws:chat:{chat.id}", str(user.id), redis)
        assert peer.id != user.id

        manager.set_typing.reset_mock()
        await stream._relay_typing(
            db_session, str(user.id), uuid4(), None, "typing", redis
        )
        manager.set_typing.assert_not_awaited()

        await stream._relay_typing(
            db_session, str(user.id), None, str(chat.id), "typing_stopped", redis
        )
        manager.clear_typing.assert_awaited_once_with(
            f"ws:chat:{chat.id}", str(user.id), redis
        )

    async def test_broadcast_presence_ignores_invalid_id_and_contains_publish_failures(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream

        user, _, _ = await _add_pair(db_session)
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock(side_effect=RuntimeError("redis unavailable"))

        await stream._broadcast_presence(db_session, "not-a-uuid", {"type": "x"}, redis)
        redis.publish.assert_not_awaited()
        await stream._broadcast_presence(
            db_session, str(user.id), {"type": "user_offline"}, redis
        )
        redis.publish.assert_awaited()

    async def test_receive_failure_still_disconnects_and_broadcasts_offline(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream

        user, _, _ = await _add_pair(db_session)
        socket = _Socket()

        async def fail_receive():
            raise RuntimeError("socket closed")

        socket.receive_text = fail_receive
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(user.id)))
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        await stream.stream_websocket(
            socket, token="opaque-test-token", redis=redis, db=db_session
        )

        manager.disconnect.assert_awaited_once_with(socket, str(user.id), redis)
        manager.broadcast_peer_presence.assert_awaited_once()
        assert manager.broadcast_peer_presence.await_args.args[1]["type"] == "user_offline"

    async def test_cancelled_connection_sends_shutdown_and_cleans_up(
        self, db_session, monkeypatch
    ):
        from app.api.v1.websocket import stream

        user, _, _ = await _add_pair(db_session)
        socket = _Socket()

        async def cancel_receive():
            raise __import__("asyncio").CancelledError

        socket.receive_text = cancel_receive
        monkeypatch.setattr(stream, "validate_ws_token", AsyncMock(return_value=str(user.id)))
        manager = _manager()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        redis = MagicMock()
        redis.publish = AsyncMock()

        with pytest.raises(__import__("asyncio").CancelledError):
            await stream.stream_websocket(
                socket, token="opaque-test-token", redis=redis, db=db_session
            )

        assert {"type": "server_shutdown"} in socket.sent
        manager.disconnect.assert_awaited_once_with(socket, str(user.id), redis)
        manager.broadcast_peer_presence.assert_awaited_once()

    async def test_endpoint_queries_only_active_chats_for_presence(self, db_session):
        from app.api.v1.websocket.stream import _broadcast_presence

        user, peer, chat = await _add_pair(db_session)
        inactive = Chat(
            id=uuid4(),
            initiator_id=user.id,
            recipient_id=peer.id,
            status="accepted",
            is_active=False,
        )
        db_session.add(inactive)
        await db_session.commit()
        redis = MagicMock()
        redis.publish = AsyncMock()
        manager = _manager()

        # Exercise the DB-backed presence broadcast helper without connecting to Redis.
        from app.api.v1.websocket import stream

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(stream, "websocket_manager", manager)
        try:
            await _broadcast_presence(
                db_session,
                str(user.id),
                {"type": "user_online", "user_id": str(user.id)},
                redis,
            )
        finally:
            monkeypatch.undo()

        redis.publish.assert_awaited_once_with(
            f"ws:chat:{chat.id}", json.dumps({"type": "user_online", "user_id": str(user.id)})
        )
