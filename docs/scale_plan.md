# Backend Scale & Production Hardening Plan
## Iranian Dating App

> Phases 1–4 of performance_plan.md are complete (indexes, caching, query optimization).
> This document covers what comes next: production hardening, scale, and missing features
> needed before real users hit the app.
>
> Work top to bottom. Each section is a separate session.

---

## Where You Stand

| Area | Status |
|------|--------|
| DB indexes (37 across 10 tables) | ✅ Done |
| Redis caching (static + user + daily limits) | ✅ Done |
| Haversine in PostgreSQL | ✅ Done |
| Eager loading / N+1 eliminated | ✅ Done |
| Cursor pagination for chat | ✅ Done |
| BackgroundTasks for notifications | ✅ Done |
| GZip middleware | ✅ Done |
| 556 tests passing | ✅ Done |
| FCM push notifications | ✅ Done (Session 34) |
| Structured logging + GlitchTip | ✅ Done (Session 33) |
| Auth hardening (token, enumeration, OTP) | ✅ Done (Session 35) |
| Location fuzzing | ✅ Done (Session 37) |
| Per-match message rate limit | ✅ Done (Session 38) |
| Daily report limit | ✅ Done (Session 39) |
| CORS fix | ✅ Done (Session 40) |
| Swipe deduplication (Redis set) | ✅ Done (Session 41) |
| Discover card stack caching | ✅ Done (Session 41) |
| Real ZarinPal payment | ❌ Missing |
| Email sending (verification, reset) | ❌ Missing |
| Connection pooling (PgBouncer) | ❌ Missing |
| Rate limiting per endpoint | ✅ Done |
| WebSocket scale (multi-process) | ❌ Missing |
| Presence (who's online) | ❌ Missing |
| Typing indicators | ❌ Missing |
| Health checks for Docker | ❌ Missing |
| Nginx reverse proxy | ❌ Missing |
| HTTPS / SSL | ❌ Missing |
| Discover card stack pre-caching | ✅ Done (Session 41) |
| Swipe deduplication (Redis set) | ✅ Done (Session 41) |
| Photo moderation pipeline | ❌ Partial |

---

## Section 1 — Connection Pooling (PgBouncer)

### The problem
FastAPI runs multiple async workers. Each worker holds open SQLAlchemy connection pool
slots. Without a connection pooler, under load (100+ concurrent users) you'll hit
PostgreSQL's `max_connections` (default: 100) and start getting:

```
asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
```

### The fix: PgBouncer in front of PostgreSQL

Add to `docker-compose.yml`:

```yaml
pgbouncer:
  image: edoburu/pgbouncer:latest
  container_name: dating_pgbouncer
  environment:
    DATABASE_URL: "postgres://dating_user:dating_pass@db:5432/dating_db"
    POOL_MODE: transaction          # best for async FastAPI
    MAX_CLIENT_CONN: 1000           # connections from FastAPI workers
    DEFAULT_POOL_SIZE: 20           # actual connections to PostgreSQL
    MIN_POOL_SIZE: 5
    RESERVE_POOL_SIZE: 5
    SERVER_RESET_QUERY: DISCARD ALL
  ports:
    - "5432:5432"                   # FastAPI connects here instead of db:5432
  depends_on:
    - db
```

Update `.env`:
```env
# FastAPI now connects to PgBouncer, not PostgreSQL directly
DATABASE_URL=postgresql+asyncpg://dating_user:dating_pass@pgbouncer:5432/dating_db
```

Update SQLAlchemy engine in `app/database.py` — disable statement cache
(incompatible with PgBouncer transaction mode):

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,           # per-worker pool (small — PgBouncer handles the real pooling)
    max_overflow=10,
    pool_pre_ping=True,    # detect dead connections
    connect_args={
        "prepared_statement_cache_size": 0,   # REQUIRED for PgBouncer transaction mode
        "statement_cache_size": 0,
    }
)
```

**Impact:** Supports 1000+ concurrent connections to FastAPI while keeping ≤20 real
PostgreSQL connections. Essential before any real traffic.

---

## Section 2 — Rate Limiting (Complete the job)

### Current state
`app/core/limiter.py` exists but the coverage is likely partial.

### What needs rate limits and at what level

| Endpoint | Limit | Reason |
|----------|-------|--------|
| `POST /auth/register/init` | 5/hour per IP | Prevent SMS/email bombing |
| `POST /auth/register/verify` | 10/hour per IP | Prevent code brute-force |
| `POST /auth/login` | 10/min per IP | Prevent password brute-force |
| `POST /auth/password-reset` | 3/hour per IP | Prevent reset bombing |
| `GET /discover` | 60/min per user | Prevent scraping |
| `GET /search` | 30/min per user | Prevent scraping |
| `POST /swipes` | 200/min per user | Supplement daily limit |
| `POST /reports/{user_id}` | 10/hour per user | Prevent report abuse |
| `GET /system/status` | 60/min per IP | Already done — verify |

### Implementation with `slowapi` (already in your stack via `limiter.py`)

```python
# app/core/limiter.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000/minute"],   # global fallback
    storage_uri=settings.REDIS_URL,   # store counts in Redis (shared across workers)
)
```

```python
# Example: auth.py
@router.post("/register/init")
@limiter.limit("5/hour")
async def register_init(request: Request, ...):
    ...

# For per-user limits (authenticated endpoints):
def get_user_key(request: Request):
    return str(request.state.user_id)   # set in middleware

@router.post("/swipes")
@limiter.limit("200/minute", key_func=get_user_key)
async def create_swipe(request: Request, ...):
    ...
```

**Critical:** Rate limit storage must be Redis, not in-memory — otherwise limits
reset when the process restarts and don't work across multiple workers.

---

## Section 3 — WebSocket Scale + Presence + Typing Indicators

### 3.1 — The Three Problems with Current `websocket_manager.py`

Your current manager (`app/services/websocket_manager.py`) has the right structure
but stores everything in local Python dicts:

```python
self.active_connections: Dict[str, Set[WebSocket]] = {}   # match notifications
self.chat_connections: Dict[str, Set[WebSocket]] = {}      # chat per match
```

This causes three separate failures:

**Problem 1 — Multi-worker delivery failure**
With `--workers 4`, user A connects to worker 1, user B to worker 3.
When A sends B a message, worker 1 has no reference to B's socket on worker 3.
Messages and match notifications silently fail to deliver.

**Problem 2 — Presence doesn't survive worker restarts**
Online status is inferred from `active_connections` dict. When a worker restarts,
the dict is empty — all users appear offline regardless of actual connections.

**Problem 3 — Typing indicators are local only**
A "typing" event from user A on worker 1 never reaches user B on worker 3.

**All three share the same root cause:** local dict storage.
**All three are fixed by the same solution:** Redis Pub/Sub + Redis presence keys.

---

### 3.2 — Architecture Overview

```
User A (worker 1)          Redis                    User B (worker 3)
     │                       │                            │
     │── send message ──────►│── PUBLISH ws:user:B ──────►│
     │                       │                            │── deliver to B's socket
     │                       │                            │
     │── typing event ───────►│── PUBLISH ws:user:B ──────►│
     │                       │                            │── show typing indicator
     │                       │                            │
     │── connect ────────────►│── SETEX online:A 60 ──────►│
     │                       │                            │── A appears online
     │                       │                            │
     │── disconnect ─────────►│── DEL online:A ───────────►│
                              │                            │── A appears offline
```

Every worker subscribes to channels for the users it holds locally.
Every publish goes to Redis — Redis fans it out to whichever worker holds the target.

---

### 3.3 — Redis Keys for WebSocket

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `ws:user:{user_id}` | Pub/Sub channel | — | Per-user message delivery channel |
| `ws:chat:{match_id}` | Pub/Sub channel | — | Per-match channel (both participants) |
| `online:{user_id}` | String | 60s | Presence — refreshed every 30s by heartbeat |
| `typing:{match_id}:{user_id}` | String | 5s | Typing indicator — expires naturally |

---

### 3.4 — Complete `websocket_manager.py` Rewrite

Replace `app/services/websocket_manager.py` entirely:

```python
import asyncio
import json
import time
from typing import Dict, Set, Optional
from uuid import UUID

from fastapi import WebSocket
from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger("websocket")

# ── Redis Key Helpers ─────────────────────────────────────────────────────────

def _user_channel(user_id: str) -> str:
    return f"ws:user:{user_id}"

def _chat_channel(match_id: str) -> str:
    return f"ws:chat:{match_id}"

def _online_key(user_id: str) -> str:
    return f"online:{user_id}"

def _typing_key(match_id: str, user_id: str) -> str:
    return f"typing:{match_id}:{user_id}"

ONLINE_TTL = 60        # seconds — presence key TTL
HEARTBEAT_INTERVAL = 30  # seconds — how often client refreshes online status
TYPING_TTL = 5         # seconds — typing indicator auto-expires


# ── WebSocketManager ──────────────────────────────────────────────────────────

class WebSocketManager:
    """
    Multi-worker WebSocket manager using Redis Pub/Sub.

    Local dicts hold THIS worker's live sockets only.
    All cross-worker delivery goes through Redis channels.
    Presence and typing state live in Redis keys (TTL-based).
    """

    def __init__(self):
        # THIS worker's connections only
        # user_id → set of WebSocket (user may have multiple tabs/devices)
        self.active_connections: Dict[str, Set[WebSocket]] = {}

        # match_id:user_id → set of WebSocket
        self.chat_connections: Dict[str, Set[WebSocket]] = {}

        # Running listener tasks — keep reference to cancel on disconnect
        self._listener_tasks: Dict[str, asyncio.Task] = {}

    # ── Match Notification Channel ────────────────────────────────────────────

    async def connect(self, websocket: WebSocket, user_id: str, redis: Redis):
        """
        Accept a match-notification WebSocket connection.
        Subscribes this worker to the user's Redis channel.
        Marks user as online.
        """
        await websocket.accept()

        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)

        # Mark online in Redis (TTL refreshed by heartbeat)
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

        # Subscribe to per-user channel and start listener task
        task_key = f"notify:{user_id}"
        if task_key not in self._listener_tasks:
            task = asyncio.create_task(
                self._listen_user_channel(user_id, redis)
            )
            self._listener_tasks[task_key] = task

        logger.info("WS connected (notifications)", user_id=user_id)

    async def disconnect(self, websocket: WebSocket, user_id: str, redis: Redis):
        """
        Remove connection. If no more sockets for this user on this worker,
        cancel the listener task and clear online status.
        """
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

                # Cancel the Redis listener for this user on this worker
                task_key = f"notify:{user_id}"
                task = self._listener_tasks.pop(task_key, None)
                if task:
                    task.cancel()

                # Remove online presence
                await redis.delete(_online_key(user_id))

        logger.info("WS disconnected (notifications)", user_id=user_id)

    async def _listen_user_channel(self, user_id: str, redis: Redis):
        """
        Long-running task: subscribe to ws:user:{user_id} and forward
        any published messages to this worker's local WebSocket(s).
        """
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
        """Send to all local sockets for a user. Clean up dead connections."""
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

    # ── Chat Channel ──────────────────────────────────────────────────────────

    async def add_chat_connection(
        self,
        websocket: WebSocket,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        """
        Accept a chat WebSocket connection for a specific match.
        Subscribes this worker to the match's Redis channel.
        Marks user as online.
        """
        await websocket.accept()

        conn_key = f"{match_id}:{user_id}"
        if conn_key not in self.chat_connections:
            self.chat_connections[conn_key] = set()
        self.chat_connections[conn_key].add(websocket)

        # Mark online
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

        # Subscribe to the match channel (one listener per match per worker)
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
        """Remove a chat connection. Cancel match listener if no local sockets remain."""
        conn_key = f"{match_id}:{user_id}"
        if conn_key in self.chat_connections:
            self.chat_connections[conn_key].discard(websocket)
            if not self.chat_connections[conn_key]:
                del self.chat_connections[conn_key]

        # Check if any local socket still connected to this match
        still_connected = any(
            k.startswith(f"{match_id}:") and v
            for k, v in self.chat_connections.items()
        )
        if not still_connected:
            task_key = f"chat:{match_id}"
            task = self._listener_tasks.pop(task_key, None)
            if task:
                task.cancel()

        # Clear online presence if user has no other connections on this worker
        user_has_other = any(
            k.endswith(f":{user_id}") and v
            for k, v in self.chat_connections.items()
        ) or user_id in self.active_connections

        if not user_has_other:
            await redis.delete(_online_key(user_id))

        logger.info("WS disconnected (chat)", match_id=match_id, user_id=user_id)

    async def _listen_chat_channel(self, match_id: str, redis: Redis):
        """
        Long-running task: subscribe to ws:chat:{match_id} and forward
        messages to any local sockets in this match.
        """
        pubsub = redis.pubsub()
        await pubsub.subscribe(_chat_channel(match_id))
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                target_user = data.get("_target_user")  # set by sender, see publish methods
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
        """
        Deliver to local chat sockets.
        If target_user is set, deliver only to that user's sockets in this match.
        Otherwise deliver to all sockets in this match.
        """
        # Remove internal routing field before sending to client
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

    # ── Publish Methods (cross-worker delivery) ───────────────────────────────

    async def send_personal_message(self, user_id: str, message: dict, redis: Redis):
        """
        Send a match notification to a user — works across all workers.
        Used for: new_match, new_message_notification, system alerts.
        """
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
        """Notify both users of a new match — works across all workers."""
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
        """
        Send a chat message to both participants — works across all workers.
        Sender gets a confirmation echo; receiver gets the full message.
        """
        # To receiver — full message
        receiver_msg = {**message, "_target_user": other_user_id}
        await redis.publish(_chat_channel(match_id), json.dumps(receiver_msg))

        # To sender — echo/confirmation
        sender_msg = {**message, "_target_user": sender_id}
        await redis.publish(_chat_channel(match_id), json.dumps(sender_msg))

    # ── Presence ──────────────────────────────────────────────────────────────

    async def is_online(self, user_id: str, redis: Redis) -> bool:
        """
        Check if a user is currently online.
        Respects hide_online_status setting — check that before calling this.
        """
        return bool(await redis.exists(_online_key(user_id)))

    async def get_online_status_bulk(
        self,
        user_ids: list[str],
        redis: Redis,
    ) -> dict[str, bool]:
        """
        Check online status for multiple users in one Redis pipeline.
        Used when loading match list — show green dot next to online matches.
        """
        pipe = redis.pipeline()
        for uid in user_ids:
            pipe.exists(_online_key(uid))
        results = await pipe.execute()
        return {uid: bool(r) for uid, r in zip(user_ids, results)}

    async def heartbeat(self, user_id: str, redis: Redis):
        """
        Refresh the online TTL. Call this every HEARTBEAT_INTERVAL seconds
        from the WebSocket handler's keepalive loop.
        Client sends {"type": "ping"} every 30s → handler calls this.
        """
        await redis.setex(_online_key(user_id), ONLINE_TTL, "1")

    # ── Typing Indicators ─────────────────────────────────────────────────────

    async def set_typing(
        self,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        """
        Mark user as typing in a match. TTL = 5s — auto-expires if they stop.
        Publishes typing event to the match channel so the other user sees it.
        """
        await redis.setex(_typing_key(match_id, user_id), TYPING_TTL, "1")

        # Publish typing event to match channel — receiver picks it up via listener
        await redis.publish(_chat_channel(match_id), json.dumps({
            "type": "typing",
            "match_id": match_id,
            "user_id": user_id,
            # _target_user not set → delivered to ALL sockets in match
            # The other participant's socket will show the indicator
            # The sender's own socket will receive it too but UI ignores self-typing events
        }))

    async def clear_typing(
        self,
        match_id: str,
        user_id: str,
        redis: Redis,
    ):
        """
        Explicitly clear typing indicator (user sent the message or left the input).
        Also published so the receiver's UI hides the indicator immediately
        rather than waiting for the 5s TTL.
        """
        await redis.delete(_typing_key(match_id, user_id))
        await redis.publish(_chat_channel(match_id), json.dumps({
            "type": "typing_stopped",
            "match_id": match_id,
            "user_id": user_id,
        }))

    async def is_typing(self, match_id: str, user_id: str, redis: Redis) -> bool:
        """Check if a user is currently typing in a match."""
        return bool(await redis.exists(_typing_key(match_id, user_id)))


# Singleton — one instance per worker process
websocket_manager = WebSocketManager()
```

---

### 3.5 — WebSocket Endpoint Updates

#### `api/v1/websocket/matches.py` — match notifications

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from app.services.websocket_manager import websocket_manager, HEARTBEAT_INTERVAL
from app.core.deps import validate_ws_token
from app.core.redis import get_redis
import asyncio, json

router = APIRouter()

@router.websocket("/ws/matches")
async def matches_websocket(
    websocket: WebSocket,
    token: str = Query(...),
    redis=Depends(get_redis),
):
    # Validate JWT before accepting — close with 4001 if invalid
    try:
        user_id = await validate_ws_token(token, redis)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket_manager.connect(websocket, user_id, redis)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=40.0)
                data = json.loads(raw)

                if data.get("type") == "ping":
                    # Refresh online presence TTL
                    await websocket_manager.heartbeat(user_id, redis)
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except asyncio.TimeoutError:
                # No message in 40s — client likely gone, close cleanly
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket_manager.disconnect(websocket, user_id, redis)
```

#### `api/v1/websocket/chat.py` — chat with presence + typing

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from uuid import UUID
from app.services.websocket_manager import websocket_manager
from app.core.deps import validate_ws_token, get_current_user_id
from app.core.redis import get_redis
from app.db.session import get_db
import asyncio, json

router = APIRouter()

@router.websocket("/ws/chat/{match_id}")
async def chat_websocket(
    websocket: WebSocket,
    match_id: str,
    token: str = Query(...),
    redis=Depends(get_redis),
    db=Depends(get_db),
):
    # Validate JWT
    try:
        user_id = await validate_ws_token(token, redis)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify user is a participant in this match (IDOR protection)
    match = await get_match_for_user(match_id, user_id, db)
    if not match:
        await websocket.close(code=4003, reason="Access denied")
        return

    other_user_id = (
        str(match.user2_id) if str(match.user1_id) == user_id
        else str(match.user1_id)
    )

    await websocket_manager.add_chat_connection(websocket, match_id, user_id, redis)

    # Notify the other user that this user is now online (if they're in the chat)
    await websocket_manager.send_to_match(
        match_id=match_id,
        sender_id=user_id,
        message={"type": "user_online", "user_id": user_id},
        other_user_id=other_user_id,
        redis=redis,
    )

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=40.0)
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "ping":
                    # Keepalive — refresh online presence
                    await websocket_manager.heartbeat(user_id, redis)
                    await websocket.send_text(json.dumps({"type": "pong"}))

                elif msg_type == "typing":
                    # User started typing — publish to match channel
                    await websocket_manager.set_typing(match_id, user_id, redis)

                elif msg_type == "typing_stopped":
                    # User stopped typing (cleared input or sent message)
                    await websocket_manager.clear_typing(match_id, user_id, redis)

                elif msg_type == "read":
                    # User read messages — notify sender via match channel
                    message_ids = data.get("message_ids", [])
                    await websocket_manager.send_to_match(
                        match_id=match_id,
                        sender_id=user_id,
                        message={
                            "type": "messages_read",
                            "message_ids": message_ids,
                            "reader_id": user_id,
                        },
                        other_user_id=other_user_id,
                        redis=redis,
                    )

                # Note: actual message sending goes through the HTTP API
                # (POST /messages/{identifier}/text) — not through WebSocket
                # WebSocket is for delivery only, not for sending

            except asyncio.TimeoutError:
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        # Notify other user this user went offline
        await websocket_manager.send_to_match(
            match_id=match_id,
            sender_id=user_id,
            message={"type": "user_offline", "user_id": user_id},
            other_user_id=other_user_id,
            redis=redis,
        )
        await websocket_manager.clear_typing(match_id, user_id, redis)
        await websocket_manager.remove_chat_connection(websocket, match_id, user_id, redis)
```

---

### 3.6 — Online Status in API Responses

Add online status to `GET /api/v1/matches` response so the match list shows
green dots without requiring a WebSocket connection just to check presence:

```python
# endpoints/matches.py
from app.services.websocket_manager import websocket_manager

@router.get("/matches")
async def get_matches(
    current_user_id: UUID = Depends(get_current_user_id),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    matches = await _fetch_matches(current_user_id, db)

    # Get other user's IDs
    other_ids = [
        str(m.user2_id) if str(m.user1_id) == str(current_user_id) else str(m.user1_id)
        for m in matches
    ]

    # Bulk online status check — one Redis pipeline call
    online_map = await websocket_manager.get_online_status_bulk(other_ids, redis)

    # Build response with is_online field
    result = []
    for match in matches:
        other_id = (
            str(match.user2_id) if str(match.user1_id) == str(current_user_id)
            else str(match.user1_id)
        )
        # Respect hide_online_status setting
        other_settings = match.user2.settings if str(match.user1_id) == str(current_user_id) \
            else match.user1.settings
        is_online = (
            online_map.get(other_id, False)
            if not other_settings.hide_online_status
            else None   # None = hidden, don't show dot
        )
        result.append({**match_to_dict(match), "is_online": is_online})

    return result
```

---

### 3.7 — Client-Side Protocol (Flutter)

This is what your Flutter WebSocket client needs to implement:

```
Connection:
  wss://api.yourapp.ir/api/v1/ws/chat/{match_id}?token=<jwt>
  wss://api.yourapp.ir/api/v1/ws/matches?token=<jwt>

Client → Server messages:
  {"type": "ping"}                          every 30s keepalive
  {"type": "typing"}                        user starts typing
  {"type": "typing_stopped"}                user stops typing or sends
  {"type": "read", "message_ids": [...]}    user read messages

Server → Client messages:
  {"type": "pong"}                          keepalive response
  {"type": "new_match", "data": {...}}      new match notification
  {"type": "new_message", "data": {...}}    new chat message delivered
  {"type": "typing", "user_id": "..."}      other user is typing
  {"type": "typing_stopped", "user_id": "..."} other user stopped
  {"type": "messages_read", "message_ids": [...], "reader_id": "..."} read receipts
  {"type": "user_online", "user_id": "..."}  other user opened the chat
  {"type": "user_offline", "user_id": "..."} other user closed the chat
```

Typing indicator logic in Flutter:
```dart
Timer? _typingTimer;

void onTextChanged(String text) {
  // Send "typing" on first keystroke
  if (_typingTimer == null) {
    _wsService.send({"type": "typing"});
  }
  // Reset timer — send "typing_stopped" after 3s of no keystrokes
  _typingTimer?.cancel();
  _typingTimer = Timer(const Duration(seconds: 3), () {
    _wsService.send({"type": "typing_stopped"});
    _typingTimer = null;
  });
}

void onMessageSent() {
  _typingTimer?.cancel();
  _typingTimer = null;
  _wsService.send({"type": "typing_stopped"});
}
```

---

### 3.8 — `validate_ws_token` Helper

Add to `app/core/deps.py`:

```python
async def validate_ws_token(token: str, redis: Redis) -> str:
    """
    Validate JWT for WebSocket connections.
    Returns user_id string or raises Exception (caller closes with 4001).
    Does NOT use get_current_user — WebSocket doesn't need full profile load.
    """
    try:
        payload = decode_access_token(token)   # your existing JWT decode
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("No sub in token")
        # Check token version via Redis (fast — no DB query)
        # If you store token_version in Redis on password change, check here
        return user_id
    except Exception:
        raise ValueError("Invalid token")
```

---

### 3.9 — Session B Checklist

- [ ] Replace `app/services/websocket_manager.py` with the rewrite above (Section 3.4)
- [ ] Update `api/v1/websocket/matches.py` with new connect/disconnect signature + ping handler (Section 3.5)
- [ ] Update `api/v1/websocket/chat.py` with typing + presence + read receipt handling (Section 3.5)
- [ ] Add `validate_ws_token` to `app/core/deps.py` (Section 3.8)
- [ ] Add `is_online` to match list response (Section 3.6)
- [ ] Update `broadcast_match()` calls in `swipes.py` to pass `redis` argument
- [ ] Update `send_to_match()` calls in `messages.py` to pass `redis` argument
- [ ] Add discover card stack pre-caching (Section 4)
- [ ] Add swipe deduplication Redis Set (Section 5)
- [ ] Test with `--workers 4`: open two browser tabs connected to different matches,
      send a message — both must receive it

---

## Section 4 — Discover Card Stack Pre-Caching

### The problem
`GET /discover` runs an expensive query on every swipe batch request:
- JOIN user_profiles
- Haversine distance filter (now in DB — good)
- Subquery to exclude swiped users (grows with swipe history)
- Subquery to exclude blocked users

As swipe history grows per user, the exclusion subquery gets slower.

### The fix: Pre-compute and cache a card stack per user

```python
# app/core/cache.py — add these

DISCOVER_STACK_TTL = 1800   # 30 minutes
DISCOVER_STACK_SIZE = 50    # pre-fetch 50 cards at a time

def key_discover_stack(user_id: UUID) -> str:
    return f"cache:discover:{user_id}:stack"

async def get_discover_stack(redis: Redis, user_id: UUID) -> list[str] | None:
    """Returns list of pre-fetched user IDs, or None if cache miss."""
    raw = await redis.get(key_discover_stack(user_id))
    return json.loads(raw) if raw else None

async def set_discover_stack(redis: Redis, user_id: UUID, user_ids: list[str]):
    await redis.setex(
        key_discover_stack(user_id),
        DISCOVER_STACK_TTL,
        json.dumps(user_ids)
    )

async def pop_from_discover_stack(redis: Redis, user_id: UUID, count: int) -> list[str]:
    """
    Pop `count` IDs from the cached stack.
    Returns empty list if stack is empty (trigger refetch).
    """
    key = key_discover_stack(user_id)
    pipe = redis.pipeline()
    pipe.lrange(key, 0, count - 1)
    pipe.ltrim(key, count, -1)
    results = await pipe.execute()
    return results[0]
```

```python
# endpoints/discover.py — updated flow

@router.get("/discover")
async def discover(limit: int = Query(20, le=50), ...):
    # 1. Try cache first
    stack = await pop_from_discover_stack(redis, current_user.id, limit)

    if not stack:
        # 2. Cache miss — run the full query for 50 users
        user_ids = await _run_discover_query(current_user, filters, limit=50, db=db)
        if len(user_ids) > limit:
            # Cache the overflow for next requests
            await set_discover_stack(redis, current_user.id, user_ids[limit:])
        stack = user_ids[:limit]

    # 3. Fetch full profiles for the IDs in the stack
    users = await _fetch_profiles_by_ids(stack, db)
    return users
```

**Invalidate stack when:** user updates location, user changes discover filters.

**Impact:** After the first discover load, subsequent loads serve from Redis.
The expensive exclusion query only runs once per 30 minutes per user.

---

## Section 5 — Swipe Deduplication (Redis Set)

### The problem
The discover query excludes swiped users via a DB subquery:
```sql
WHERE u.id NOT IN (SELECT swipee_id FROM swipes WHERE swiper_id = :user_id)
```

A power user who swipes 500 people/day generates a subquery scanning 500+ rows
on every discover load. At 10,000 total swipes, this is noticeably slow even with
the index.

### The fix: Mirror swipe history in a Redis Set

```python
# After every swipe, add to Redis set:
async def record_swipe_cache(redis: Redis, swiper_id: UUID, swipee_id: UUID):
    key = f"swiped:{swiper_id}"
    await redis.sadd(key, str(swipee_id))
    await redis.expire(key, 7 * 86400)   # 7 days TTL

async def get_swiped_ids(redis: Redis, user_id: UUID) -> set[str]:
    members = await redis.smembers(f"swiped:{user_id}")
    return {m.decode() for m in members}
```

In `discover.py`, replace the DB subquery with a Redis set lookup:

```python
swiped_ids = await get_swiped_ids(redis, current_user.id)
stmt = stmt.where(User.id.not_in(swiped_ids))
```

---

## Section 6 — Nginx + SSL (Required Before Production)

### docker-compose.yml addition

```yaml
nginx:
  image: nginx:alpine
  container_name: dating_nginx
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/ssl:/etc/nginx/ssl:ro
    - ./nginx/certbot:/var/www/certbot
  depends_on:
    - api
```

### `nginx/nginx.conf`

```nginx
upstream api {
    server api:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name api.yourapp.ir;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.yourapp.ir;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;

    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000" always;

    client_max_body_size 12M;

    # REST API
    location /api/ {
        proxy_pass http://api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Connection "";
        proxy_read_timeout 60s;
    }

    # WebSocket — MUST have Upgrade headers
    location /api/v1/ws/ {
        proxy_pass http://api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

### Get SSL Certificate

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d api.yourapp.ir
```

---

## Section 7 — Structured Logging + Error Tracking

```python
# app/core/logging.py — replace with structlog
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)

def get_logger(name: str):
    return structlog.get_logger(name)
```

```python
# app/main.py — add Sentry
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    integrations=[FastApiIntegration(), SqlalchemyIntegration()],
    traces_sample_rate=0.1,
    environment=settings.ENVIRONMENT,
)
```

---

## Section 8 — Docker Health Checks + Multi-Worker Config

```yaml
services:
  api:
    command: >
      uvicorn app.main:app
      --host 0.0.0.0
      --port 8000
      --workers 4
      --worker-class uvicorn.workers.UvicornWorker
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/auth/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped

  db:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dating_user -d dating_db"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5
```

Workers: `(2 × CPU cores) + 1`

**Note:** With multiple workers, Section 3 (Redis Pub/Sub) is mandatory.

---

## Section 9 — FCM Push Notifications

**`app/models/device_token.py`**
```python
class DeviceToken(Base):
    __tablename__ = "device_tokens"
    id = Column(UUID, primary_key=True, default=uuid4)
    user_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    token = Column(String, nullable=False)
    platform = Column(String(10))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "token"),
        Index("idx_device_tokens_user", "user_id"),
    )
```

**`app/services/push_service.py`**
```python
import firebase_admin
from firebase_admin import credentials, messaging

class PushService:
    def __init__(self):
        cred = credentials.Certificate(settings.FCM_SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred)

    async def send_to_user(self, user_id: UUID, title: str, body: str, data: dict, db: AsyncSession):
        tokens = await self._get_user_tokens(user_id, db)
        if not tokens:
            return
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in data.items()},
            android=messaging.AndroidConfig(priority="high"),
        )
        response = messaging.send_each_for_multicast(message)
        await self._cleanup_failed_tokens(tokens, response, db)
```

**New endpoint:** `POST /api/v1/notifications/device-token`

**Add to `.env`:**
```env
FCM_SERVICE_ACCOUNT_PATH=/app/firebase-service-account.json
```

---

## Section 10 — ZarinPal Real Payment

```python
# app/services/payment_service.py
import httpx

ZARINPAL_REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
ZARINPAL_VERIFY_URL  = "https://api.zarinpal.com/pg/v4/payment/verify.json"
ZARINPAL_START_URL   = "https://www.zarinpal.com/pg/StartPay/{authority}"

class ZarinpalService:
    async def create_payment(self, amount_toman: int, description: str, callback_url: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(ZARINPAL_REQUEST_URL, json={
                "merchant_id": settings.ZARINPAL_MERCHANT_ID,
                "amount": amount_toman * 10,
                "description": description,
                "callback_url": callback_url,
            })
        data = response.json()
        if data["data"]["code"] != 100:
            raise PaymentError(f"ZarinPal error: {data['errors']}")
        authority = data["data"]["authority"]
        return {
            "authority": authority,
            "payment_url": ZARINPAL_START_URL.format(authority=authority),
        }

    async def verify_payment(self, authority: str, amount_toman: int) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(ZARINPAL_VERIFY_URL, json={
                "merchant_id": settings.ZARINPAL_MERCHANT_ID,
                "amount": amount_toman * 10,
                "authority": authority,
            })
        data = response.json()
        code = data["data"]["code"]
        if code not in (100, 101):
            raise PaymentError(f"Verification failed: {data['errors']}")
        return str(data["data"]["ref_id"])
```

---

## Implementation Order

### Session A — Connection Pooling + Rate Limiting
- [ ] Add PgBouncer to `docker-compose.yml` (Section 1)
- [ ] Update SQLAlchemy engine with `prepared_statement_cache_size=0` (Section 1)
- [ ] Audit `limiter.py` — confirm Redis storage, not in-memory (Section 2)
- [ ] Add rate limits to all auth endpoints (Section 2)
- [ ] Add rate limits to discover/search (Section 2)

### Session B — WebSocket Scale + Presence + Typing + Discover Cache
- [ ] Replace `websocket_manager.py` with full rewrite (Section 3.4)
- [ ] Update `matches.py` websocket endpoint (Section 3.5)
- [ ] Update `chat.py` websocket endpoint with typing + presence + read receipts (Section 3.5)
- [ ] Add `validate_ws_token` to `deps.py` (Section 3.8)
- [ ] Add `is_online` field to match list response (Section 3.6)
- [ ] Update all `broadcast_match()` and `send_to_match()` callers to pass `redis`
- [ ] Add discover card stack pre-caching (Section 4)
- [ ] Add swipe deduplication Redis Set (Section 5)
- [ ] Test with `--workers 4`: verify messages, typing, and presence work across workers

### Session C — Nginx + SSL + Docker Production Config
- [ ] Write `nginx/nginx.conf` with WebSocket proxy headers (Section 6)
- [ ] Get SSL cert via Let's Encrypt (Section 6)
- [ ] Add health checks to all services (Section 8)
- [ ] Set `--workers 4` in production compose command (Section 8)

### Session D — Observability
- [ ] Replace logging with `structlog` JSON output (Section 7)
- [ ] Add Sentry SDK to `main.py` (Section 7)
- [ ] Add `SENTRY_DSN` to `.env` (Section 7)

### Session E — FCM Push Notifications
- [ ] Create `DeviceToken` model + migration (Section 9)
- [ ] Build `push_service.py` (Section 9)
- [ ] Add `POST /notifications/device-token` endpoint (Section 9)
- [ ] Wire into `notification_service.py` via BackgroundTasks (Section 9)

### Session F — Real ZarinPal Payment
- [ ] Build `payment_service.py` (Section 10)
- [ ] Replace mock in `subscriptions.py` (Section 10)
- [ ] Add `GET /subscriptions/verify` callback endpoint (Section 10)

---

## Priority Order

| Priority | Item | Why |
|----------|------|-----|
| 🔴 Must before launch | Nginx + SSL (Section 6) | HTTPS required for Play Store |
| 🔴 Must before launch | Docker health checks + workers (Section 8) | Crashes won't auto-recover |
| 🔴 Must before launch | FCM push notifications (Section 9) | Dating apps die without alerts |
| 🔴 Must before launch | Real ZarinPal (Section 10) | Can't monetize without it |
| 🟡 Do before scaling | PgBouncer (Section 1) | Needed at 100+ concurrent users |
| 🟡 Do before scaling | Rate limiting audit (Section 2) | Needed before public exposure |
| 🟡 Do before scaling | WebSocket rewrite — Section 3 | Needed with multiple workers |
| 🟡 Do before scaling | Presence + typing — Section 3 | Core UX for a chat app |
| 🟢 Nice to have | Sentry + structlog (Section 7) | Production visibility |
| 🟢 Nice to have | Discover card stack cache (Section 4) | UX at scale |
| 🟢 Nice to have | Swipe Redis set (Section 5) | Needed at 10,000+ swipes/user |