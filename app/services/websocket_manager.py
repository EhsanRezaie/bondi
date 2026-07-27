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


def _user_channel(user_id: str) -> str:
    return f"ws:user:{user_id}"


def _chat_channel(match_id: str) -> str:
    return f"ws:chat:{match_id}"


def _online_key(user_id: str) -> str:
    return f"online:{user_id}"


def _typing_key(match_id: str, user_id: str) -> str:
    return f"typing:{match_id}:{user_id}"


class WebSocketManager:
    """Multi-worker WebSocket manager using Redis Pub/Sub."""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.chat_connections: Dict[str, Set[WebSocket]] = {}
        self._listener_tasks: Dict[str, asyncio.Task] = {}

    # ── Match Notification Channel ──────────────────────────────────────

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
            self._listener_tasks[task_key] = task

        logger.info("WS connected (notifications)", user_id=user_id)

    async def disconnect(self, websocket: WebSocket, user_id: str, redis: Redis):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

                task_key = f"notify:{user_id}"
                task = self._listener_tasks.pop(task_key, None)
                if task:
                    task.cancel()

                await redis.delete(_online_key(user_id))

        logger.info("WS disconnected (notifications)", user_id=user_id)

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
        finally:
            await pubsub.unsubscribe(_user_channel(user_id))
            await pubsub.aclose()

    async def _deliver_to_user_local(self, user_id: str, message: dict):
        sockets = self.active_connections.get(user_id, set()).copy()
        dead = []
        data = json.dumps(message)
        for ws in sockets:
            try:
                await ws.send_text(data)
            except Exception as e:
                logger.warning("WS send failed", user_id=user_id, error=str(e))
                dead.append(ws)
        for ws in dead:
            self.active_connections.get(user_id, set()).discard(ws)

    # ── Chat Channel ──────────────────────────────────────────────────

    async def add_chat_connection(
        self,
        websocket: WebSocket,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        await websocket.accept()

        conn_key = f"{match_id}:{user_id}"
        if conn_key not in self.chat_connections:
            self.chat_connections[conn_key] = set()
        self.chat_connections[conn_key].add(websocket)

        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

        task_key = f"chat:{match_id}"
        if task_key not in self._listener_tasks:
            task = asyncio.create_task(
                self._listen_chat_channel(match_id, redis)
            )
            self._listener_tasks[task_key] = task

        logger.info("WS connected (chat)", match_id=match_id, user_id=user_id)

    async def remove_chat_connection(
        self,
        websocket: WebSocket,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        conn_key = f"{match_id}:{user_id}"
        if conn_key in self.chat_connections:
            self.chat_connections[conn_key].discard(websocket)
            if not self.chat_connections[conn_key]:
                del self.chat_connections[conn_key]

        still_connected = any(
            k.startswith(f"{match_id}:") and v
            for k, v in self.chat_connections.items()
        )
        if not still_connected:
            task_key = f"chat:{match_id}"
            task = self._listener_tasks.pop(task_key, None)
            if task:
                task.cancel()

        user_has_other = any(
            k.endswith(f":{user_id}") and v
            for k, v in self.chat_connections.items()
        ) or user_id in self.active_connections

        if not user_has_other:
            await redis.delete(_online_key(user_id))

        logger.info("WS disconnected (chat)", match_id=match_id, user_id=user_id)

    async def _listen_chat_channel(self, match_id: str, redis: Redis):
        pubsub = redis.pubsub()
        await pubsub.subscribe(_chat_channel(match_id))
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                target_user = data.get("_target_user")
                await self._deliver_to_chat_local(match_id, data, target_user)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(_chat_channel(match_id))
            await pubsub.aclose()

    async def _deliver_to_chat_local(
        self,
        match_id: str,
        message: dict,
        target_user: Optional[str] = None,
    ):
        message.pop("_target_user", None)
        data = json.dumps(message)

        for conn_key, sockets in list(self.chat_connections.items()):
            m_id, u_id = conn_key.split(":", 1)
            if m_id != match_id:
                continue
            if target_user and u_id != target_user:
                continue
            dead = []
            for ws in sockets.copy():
                try:
                    await ws.send_text(data)
                except Exception as e:
                    logger.warning("Chat send failed", conn_key=conn_key, error=str(e))
                    dead.append(ws)
            for ws in dead:
                sockets.discard(ws)

    # ── Publish Methods (cross-worker delivery) ───────────────────────────

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

    # ── Presence ──────────────────────────────────────────────────────

    async def is_online(self, user_id: str, redis: Redis) -> bool:
        return bool(await redis.exists(_online_key(user_id)))

    async def get_online_status_bulk(
        self,
        user_ids: list,
        redis: Redis,
    ) -> dict:
        pipe = redis.pipeline()
        for uid in user_ids:
            pipe.exists(_online_key(uid))
        results = await pipe.execute()
        return {uid: bool(r) for uid, r in zip(user_ids, results)}

    async def heartbeat(self, user_id: str, redis: Redis):
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

    # ── Typing Indicators ─────────────────────────────────────────────

    async def set_typing(
        self,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        await redis.setex(_typing_key(match_id, user_id), TYPING_TTL, "1")
        await redis.publish(_chat_channel(match_id), json.dumps({
            "type": "typing",
            "match_id": match_id,
            "user_id": user_id,
        }))

    async def clear_typing(
        self,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        await redis.delete(_typing_key(match_id, user_id))
        await redis.publish(_chat_channel(match_id), json.dumps({
            "type": "typing_stopped",
            "match_id": match_id,
            "user_id": user_id,
        }))

    async def is_typing(self, match_id: str, user_id: str, redis: Redis) -> bool:
        return bool(await redis.exists(_typing_key(match_id, user_id)))


websocket_manager = WebSocketManager()