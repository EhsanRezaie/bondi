import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.services.websocket_manager import ONLINE_TTL, WebSocketManager


class _PubSub:
    def __init__(self, messages=()):
        self.messages = messages
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def aclose(self):
        self.closed = True

    async def listen(self):
        for message in self.messages:
            yield message


class TestWebSocketManager:
    async def test_connect_registers_socket_listener_online_state_and_presence(self, monkeypatch):
        manager = WebSocketManager()
        websocket = AsyncMock()
        redis = AsyncMock()
        presence = AsyncMock()
        monkeypatch.setattr(manager, "broadcast_peer_presence", presence)

        async def listener(_user_id, _redis):
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_listen_user_channel", listener)
        await manager.connect(websocket, "u1", redis)

        websocket.accept.assert_awaited_once()
        assert manager.active_connections["u1"] == {websocket}
        redis.setex.assert_awaited_once_with("online:u1", ONLINE_TTL, "1")
        presence.assert_awaited_once_with("u1", {"type": "user_online", "user_id": "u1"}, redis)
        task = manager._listener_tasks.pop("notify:u1")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_disconnect_only_clears_state_after_last_socket(self, monkeypatch):
        manager = WebSocketManager()
        first, second = AsyncMock(), AsyncMock()
        manager.active_connections["u1"] = {first, second}
        manager.user_subscriptions["u1"] = {"chat-1": "u2"}
        manager._chat_subscribers["chat-1"] = {"u1"}
        manager.peer_index["u2"] = {"chat-1"}
        task = MagicMock()
        manager._listener_tasks["notify:u1"] = task
        release = Mock()
        monkeypatch.setattr(manager, "_release_chat_listener_if_unused", release)
        redis = AsyncMock()

        await manager.disconnect(first, "u1", redis)
        assert manager.active_connections["u1"] == {second}
        redis.delete.assert_not_awaited()
        task.cancel.assert_not_called()

        await manager.disconnect(second, "u1", redis)
        assert "u1" not in manager.active_connections
        assert "u1" not in manager.user_subscriptions
        assert "chat-1" not in manager._chat_subscribers
        assert "u2" not in manager.peer_index
        release.assert_called_once_with("chat-1")
        task.cancel.assert_called_once()
        redis.delete.assert_awaited_once_with("online:u1")

    async def test_presence_broadcast_publishes_once_per_peer_chat(self):
        manager = WebSocketManager()
        manager.peer_index["u1"] = {"chat-a", "chat-b"}
        redis = AsyncMock()

        await manager.broadcast_peer_presence("u1", {"type": "user_online"}, redis)

        assert {call.args[0] for call in redis.publish.await_args_list} == {
            "ws:chat:chat-a",
            "ws:chat:chat-b",
        }

    async def test_user_listener_skips_control_frames_delivers_and_closes(self, monkeypatch):
        manager = WebSocketManager()
        pubsub = _PubSub(
            [
                {"type": "subscribe", "data": 1},
                {"type": "message", "data": json.dumps({"type": "new_match"})},
            ]
        )
        redis = MagicMock()
        redis.pubsub.return_value = pubsub
        deliver = AsyncMock()
        monkeypatch.setattr(manager, "_deliver_to_user_local", deliver)

        await manager._listen_user_channel("u1", redis)

        assert pubsub.subscribed == ["ws:user:u1"]
        assert pubsub.unsubscribed == ["ws:user:u1"]
        assert pubsub.closed
        deliver.assert_awaited_once_with("u1", {"type": "new_match"})

    async def test_chat_listener_routes_target_and_local_delivery_strips_internal_field(
        self, monkeypatch
    ):
        manager = WebSocketManager()
        pubsub = _PubSub(
            [
                {"type": "subscribe", "data": 1},
                {
                    "type": "message",
                    "data": json.dumps({"type": "new_message", "_target_user": "u2"}),
                },
            ]
        )
        redis = MagicMock()
        redis.pubsub.return_value = pubsub
        deliver = AsyncMock()
        local_delivery = manager._deliver_to_chat_local
        monkeypatch.setattr(manager, "_deliver_to_chat_local", deliver)

        await manager._listen_chat_channel("ws:chat:c1", redis)

        deliver.assert_awaited_once_with(
            "ws:chat:c1", {"type": "new_message", "_target_user": "u2"}, "u2"
        )
        assert pubsub.unsubscribed == ["ws:chat:c1"]
        assert pubsub.closed

        manager._chat_subscribers["c1"] = {"u1", "u2"}
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager.active_connections = {"u1": {ws1}, "u2": {ws2}}
        frame = {"type": "new_message", "_target_user": "u2"}
        await local_delivery("ws:chat:c1", frame, "u2")
        ws1.send_text.assert_not_awaited()
        ws2.send_text.assert_awaited_once_with('{"type": "new_message"}')
        assert "_target_user" not in frame

    async def test_personal_delivery_prunes_failed_sockets_and_publish_shapes(self):
        manager = WebSocketManager()
        broken, working = AsyncMock(), AsyncMock()
        broken.send_text.side_effect = RuntimeError("closed")
        manager.active_connections["u1"] = {broken, working}

        await manager._deliver_to_user_local("u1", {"type": "notice"})
        assert broken not in manager.active_connections["u1"]
        working.send_text.assert_awaited_once_with('{"type": "notice"}')
        await manager._deliver_to_user_local("missing", {"type": "notice"})

        redis = AsyncMock()
        await manager.send_personal_message("u1", {"type": "notice"}, redis)
        await manager.broadcast_match("u1", "u2", "m1", {"id": "u1"}, {"id": "u2"}, redis)
        await manager.send_to_match("m1", "u1", {"type": "new_message"}, "u2", redis)
        assert redis.publish.await_count == 5

        published = [json.loads(call.args[1]) for call in redis.publish.await_args_list]
        assert published[1] == {
            "type": "new_match",
            "data": {"match_id": "m1", "user": {"id": "u2"}},
        }
        assert published[2]["data"]["user"] == {"id": "u1"}
        assert published[3]["_target_user"] == "u2"
        assert published[4]["_target_user"] == "u1"

    async def test_subscribe_reuses_listener_and_unsubscribe_cleans_reverse_indexes(
        self, monkeypatch
    ):
        manager = WebSocketManager()
        listener_started = asyncio.Event()

        async def listener(_channel, _redis):
            listener_started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_listen_chat_channel", listener)
        redis = AsyncMock()

        await manager.subscribe("u1", "c1", "u2", redis)
        task = manager._listener_tasks["chat:ws:chat:c1"]
        await manager.subscribe("u1", "c1", "u2", redis)
        await listener_started.wait()
        assert manager._listener_tasks["chat:ws:chat:c1"] is task
        redis.setex.assert_awaited_with("online:u1", ONLINE_TTL, "1")

        await manager.unsubscribe("u1", "c1")
        assert manager.user_subscriptions == {}
        assert manager._chat_subscribers == {}
        assert manager.peer_index == {}
        assert "chat:ws:chat:c1" not in manager._listener_tasks
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_listener_exceptions_are_contained_and_pubsub_closed(self):
        manager = WebSocketManager()
        pubsub = _PubSub([{"type": "message", "data": "not-json"}])
        redis = MagicMock()
        redis.pubsub.return_value = pubsub

        await manager._listen_user_channel("u1", redis)

        assert pubsub.unsubscribed == ["ws:user:u1"]
        assert pubsub.closed

    async def test_typing_normalizes_channel_and_adds_optional_target(self):
        manager = WebSocketManager()
        redis = AsyncMock()

        await manager.set_typing("c1", "u1", redis, "u2")
        await manager.clear_typing("ws:chat:c1", "u1", redis)

        set_channel, set_data = redis.publish.await_args_list[0].args
        clear_channel, clear_data = redis.publish.await_args_list[1].args
        assert set_channel == clear_channel == "ws:chat:c1"
        assert json.loads(set_data) == {
            "type": "typing",
            "chat_id": "c1",
            "user_id": "u1",
            "_target_user": "u2",
        }
        assert json.loads(clear_data) == {
            "type": "typing_stopped",
            "chat_id": "c1",
            "user_id": "u1",
        }

    async def test_bulk_online_status_maps_pipeline_results(self):
        manager = WebSocketManager()
        pipeline = MagicMock()
        pipeline.exists = Mock()
        pipeline.execute = AsyncMock()
        pipeline.execute.return_value = [1, 0, 2]
        redis = MagicMock()
        redis.pipeline.return_value = pipeline

        result = await manager.get_online_status_bulk(["u1", "u2", "u3"], redis)

        assert result == {"u1": True, "u2": False, "u3": True}
        assert [call.args[0] for call in pipeline.exists.call_args_list] == [
            "online:u1",
            "online:u2",
            "online:u3",
        ]
