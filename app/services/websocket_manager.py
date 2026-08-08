import asyncio
import json
from typing import Dict, Set, Optional
from uuid import UUID

from fastapi import WebSocket
from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger("websocket")

ONLINE_TTL = 60
HEARTBEAT_INTERVAL = 30
TYPING_TTL = 5

_USER_PREFIX = "ws:user:"
_CHAT_PREFIX = "ws:chat:"


def _user_channel(user_id: str) -> str:
    return f"{_USER_PREFIX}{user_id}"


def _chat_channel(chat_id: str) -> str:
    return f"{_CHAT_PREFIX}{chat_id}"


def _chat_id_from_channel(channel: str) -> str:
    if channel.startswith(_CHAT_PREFIX):
        return channel[len(_CHAT_PREFIX):]
    return channel


def _online_key(user_id: str) -> str:
    return f"online:{user_id}"


def _typing_key(match_id: str, user_id: str) -> str:
    return f"typing:{match_id}:{user_id}"


class WebSocketManager:
    """Multi-worker WebSocket manager using Redis Pub/Sub.

    Single-session-socket model: each user keeps one persistent connection
    (`/ws/stream`). Delivery is gated by *topic subscriptions* — a chat's
    events (`new_message`, typing, presence, read) are only forwarded to a user
    while they have that chat open (subscribed). Personal-channel events
    (`new_match`, `chat_updated`, `chat_accepted`) arrive on the same socket.
    """

    def __init__(self):
        # user_id -> set of live sockets (normally 1 per device)
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # user_id -> {chat_id: peer_user_id}  (open chats / topic subscriptions)
        self.user_subscriptions: Dict[str, Dict[str, str]] = {}
        self._listener_tasks: Dict[str, asyncio.Task] = {}

    # ── Session Socket ─────────────────────────────────────────────────

    def _log_listener_failure(self, task_key: str, task: "asyncio.Task"):
        """Surface unhandled exceptions from background listener tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "ws_listener_task_failed",
                task_key=task_key,
                error=str(exc),
                exc_info=exc,
            )

    async def connect(self, websocket: WebSocket, user_id: str, redis: Redis):
        await websocket.accept()

        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)

        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

        task_key = f"notify:{user_id}"
        if task_key not in self._listener_tasks:
            task = asyncio.create_task(
                self._listen_user_channel(user_id, redis)
            )
            task.add_done_callback(lambda t: self._log_listener_failure(task_key, t))
            self._listener_tasks[task_key] = task

        # Let peers who currently have a chat open with this user flip online.
        await self.broadcast_peer_presence(
            user_id, {"type": "user_online", "user_id": user_id}, redis
        )

        logger.info("WS connected (session)", user_id=user_id)

    async def disconnect(self, websocket: WebSocket, user_id: str, redis: Redis):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

                # Drop topic subscriptions and their now-unused channel listeners.
                subs = self.user_subscriptions.pop(user_id, None)
                for chat_id in (subs or {}):
                    self._release_chat_listener_if_unused(chat_id)

                task_key = f"notify:{user_id}"
                task = self._listener_tasks.pop(task_key, None)
                if task:
                    task.cancel()

                await redis.delete(_online_key(user_id))

        logger.info("WS disconnected (session)", user_id=user_id)

    # ── Chat Channel Resolution ───────────────────────────────────────

    def conversation_channel(
        self, identifier, current_user_id=None, other_user_id=None
    ) -> str:
        """Resolve the canonical Redis pub/sub channel for a chat.
        Always ws:chat:{chat_id}. The id is a chat id."""
        return _chat_channel(str(identifier))

    # ── Subscriptions (topic routing) ─────────────────────────────────

    async def subscribe(
        self,
        user_id: str,
        chat_id: str,
        peer_user_id: str,
        redis: Redis,
    ):
        """Open a chat on the session socket (start receiving its events)."""
        if user_id not in self.user_subscriptions:
            self.user_subscriptions[user_id] = {}
        self.user_subscriptions[user_id][chat_id] = peer_user_id

        await self._ensure_chat_listener(_chat_channel(chat_id), redis)
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

    async def unsubscribe(self, user_id: str, chat_id: str):
        subs = self.user_subscriptions.get(user_id)
        if subs:
            subs.pop(chat_id, None)
            if not subs:
                self.user_subscriptions.pop(user_id, None)

        self._release_chat_listener_if_unused(chat_id)

    # ── Presence ───────────────────────────────────────────────────────

    async def broadcast_peer_presence(
        self, user_id: str, payload: dict, redis: Redis
    ):
        """Push a presence event (online/offline) to every open chat whose
        peer is `user_id`, so that peer's open screens update in real time."""
        for _subscriber, subs in list(self.user_subscriptions.items()):
            for chat_id, peer in subs.items():
                if peer == user_id:
                    channel = _chat_channel(chat_id)
                    await redis.publish(channel, json.dumps(payload))

    async def is_online(self, user_id: str, redis: Redis) -> bool:
        return bool(await redis.exists(_online_key(user_id)))

    async def get_online_status_bulk(self, user_ids: list, redis: Redis) -> dict:
        pipe = redis.pipeline()
        for uid in user_ids:
            pipe.exists(_online_key(uid))
        results = await pipe.execute()
        return {uid: bool(r) for uid, r in zip(user_ids, results)}

    # ── User channel listener (personal events) ───────────────────────

    async def _listen_user_channel(self, user_id: str, redis: Redis):
        pubsub = redis.pubsub()
        await pubsub.subscribe(_user_channel(user_id))
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                await self._deliver_to_user_local(user_id, data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws_user_listener_failed", user_id=user_id, error=str(e), exc_info=True)
        finally:
            await pubsub.unsubscribe(_user_channel(user_id))
            await pubsub.aclose()

    async def _deliver_to_user_local(self, user_id: str, message: dict):
        data = json.dumps(message)
        sockets = self.active_connections.get(user_id)
        if not sockets:
            return
        for ws in list(sockets):
            try:
                await ws.send_text(data)
            except Exception as e:
                logger.warning("User send failed", user_id=user_id, error=str(e))
                sockets.discard(ws)

    # ── Chat channel listeners ─────────────────────────────────────────

    async def _ensure_chat_listener(self, channel: str, redis: Redis):
        task_key = f"chat:{channel}"
        if task_key not in self._listener_tasks:
            task = asyncio.create_task(self._listen_chat_channel(channel, redis))
            task.add_done_callback(lambda t: self._log_listener_failure(task_key, t))
            self._listener_tasks[task_key] = task

    def _release_chat_listener_if_unused(self, chat_id_or_channel: str):
        channel = (
            chat_id_or_channel
            if chat_id_or_channel.startswith(_CHAT_PREFIX)
            else _chat_channel(chat_id_or_channel)
        )
        chat_id = _chat_id_from_channel(channel)
        still_subscribed = any(
            chat_id in subs for subs in self.user_subscriptions.values()
        )
        if still_subscribed:
            return
        task_key = f"chat:{channel}"
        task = self._listener_tasks.pop(task_key, None)
        if task:
            task.cancel()

    async def _listen_chat_channel(self, channel: str, redis: Redis):
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                target_user = data.get("_target_user")
                await self._deliver_to_chat_local(channel, data, target_user)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws_chat_listener_failed", channel=channel, error=str(e), exc_info=True)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def _deliver_to_chat_local(
        self,
        channel: str,
        message: dict,
        target_user: Optional[str] = None,
    ):
        """Forward a chat-channel event only to users currently subscribed to
        that chat, pruning dead sockets as it goes (fixes send-after-close)."""
        chat_id = _chat_id_from_channel(channel)
        message.pop("_target_user", None)
        data = json.dumps(message)

        for user_id, subs in list(self.user_subscriptions.items()):
            if chat_id not in subs:
                continue
            if target_user and user_id != target_user:
                continue
            sockets = self.active_connections.get(user_id)
            if not sockets:
                continue
            for ws in list(sockets):
                try:
                    await ws.send_text(data)
                except Exception as e:
                    logger.warning(
                        "Chat send failed", user_id=user_id, error=str(e)
                    )
                    sockets.discard(ws)

    # ── Publish Methods (cross-worker delivery) ────────────────────────

    async def send_personal_message(self, user_id: str, message: dict, redis: Redis):
        await redis.publish(_user_channel(user_id), json.dumps(message))

    async def broadcast_match(
        self,
        user1_id: str,
        user2_id: str,
        match_id: str,
        user1_data: dict,
        user2_data: dict,
        redis: Redis,
    ):
        await self.send_personal_message(user1_id, {
            "type": "new_match",
            "data": {"match_id": match_id, "user": user2_data}
        }, redis)
        await self.send_personal_message(user2_id, {
            "type": "new_match",
            "data": {"match_id": match_id, "user": user1_data}
        }, redis)
        logger.info("Match broadcast published", match_id=match_id)

    async def send_to_match(
        self,
        match_id: str,
        sender_id: str,
        message: dict,
        other_user_id: str,
        redis: Redis,
    ):
        receiver_msg = {**message, "_target_user": other_user_id}
        await redis.publish(_chat_channel(match_id), json.dumps(receiver_msg))

        sender_msg = {**message, "_target_user": sender_id}
        await redis.publish(_chat_channel(match_id), json.dumps(sender_msg))

    async def send_to_conversation(
        self,
        channel: str,
        sender_id: str,
        message: dict,
        other_user_id: str,
        redis: Redis,
    ):
        """Publish a message onto an already-resolved channel (matched or unmatched)."""
        receiver_msg = {**message, "_target_user": other_user_id}
        await redis.publish(channel, json.dumps(receiver_msg))

        sender_msg = {**message, "_target_user": sender_id}
        await redis.publish(channel, json.dumps(sender_msg))

    # ── Heartbeat ─────────────────────────────────────────────────────

    async def heartbeat(self, user_id: str, redis: Redis):
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

    # ── Typing Indicators ─────────────────────────────────────────────

    async def set_typing(
        self,
        channel: str,
        user_id: str,
        redis: Redis,
        target_user: Optional[str] = None,
    ):
        await redis.setex(_typing_key(channel, user_id), TYPING_TTL, "1")
        payload = {
            "type": "typing",
            "chat_id": _chat_id_from_channel(channel),
            "user_id": user_id,
        }
        if target_user:
            payload["_target_user"] = target_user
        await redis.publish(channel, json.dumps(payload))

    async def clear_typing(
        self,
        channel: str,
        user_id: str,
        redis: Redis,
        target_user: Optional[str] = None,
    ):
        await redis.delete(_typing_key(channel, user_id))
        payload = {
            "type": "typing_stopped",
            "chat_id": _chat_id_from_channel(channel),
            "user_id": user_id,
        }
        if target_user:
            payload["_target_user"] = target_user
        await redis.publish(channel, json.dumps(payload))

    async def is_typing(self, channel: str, user_id: str, redis: Redis) -> bool:
        return bool(await redis.exists(_typing_key(channel, user_id)))


websocket_manager = WebSocketManager()