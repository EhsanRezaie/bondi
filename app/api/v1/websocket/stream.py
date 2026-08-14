import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, or_
from app.services.websocket_manager import websocket_manager
from app.core.deps import validate_ws_token
from app.core.redis import get_redis
from app.db.session import get_db
from app.models.chat import Chat
from app.models.user import User
from app.models.user_settings import UserSettings
from app.models.block import Block
from app.core.logging import get_logger

logger = get_logger("websocket")

router = APIRouter()

RECEIVE_TIMEOUT = 40.0


async def _resolve_chat(
    db: AsyncSession, chat_id: str, user_id: str
):
    """Validate a chat exists, is active, the user is a member, and return the
    canonical channel + other participant. Returns None if not authorized."""
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as e:
        logger.warning("ws_chat_uuid_invalid", chat_id=chat_id, error=str(e), exc_info=True)
        return None

    chat_obj = (
        await db.execute(
            select(Chat).where(
                Chat.id == chat_uuid,
                Chat.is_active == True,
                or_(
                    Chat.initiator_id == user_id,
                    Chat.recipient_id == user_id,
                ),
            )
        )
    ).scalar_one_or_none()
    if not chat_obj:
        return None

    other_user_id = (
        str(chat_obj.recipient_id) if str(chat_obj.initiator_id) == user_id
        else str(chat_obj.initiator_id)
    )

    blocked = await db.scalar(
        select(Block.id).where(
            or_(
                (Block.blocker_id == user_id) & (Block.blocked_id == other_user_id),
                (Block.blocker_id == other_user_id) & (Block.blocked_id == user_id),
            )
        ).limit(1)
    )
    if blocked:
        return None

    return str(chat_obj.id), other_user_id


async def _presence_snapshot(
    db: AsyncSession, peer_user_id: str, redis, chat_id: str
):
    """Send the current online/last-seen state of the peer over the calling
    socket (delivered directly by the caller). Returns the event dict."""
    online = await websocket_manager.is_online(peer_user_id, redis)
    last_seen_at = None
    hide_last_seen = False
    ts = await db.scalar(select(User.last_seen_at).where(User.id == peer_user_id))
    if ts:
        last_seen_at = ts.isoformat()
    settings = await db.scalar(
        select(UserSettings.hide_last_seen).where(UserSettings.user_id == peer_user_id)
    )
    hide_last_seen = bool(settings)

    if online:
        return {
            "type": "user_online",
            "chat_id": chat_id,
            "user_id": peer_user_id,
        }
    return {
        "type": "user_offline",
        "chat_id": chat_id,
        "user_id": peer_user_id,
        "last_seen_at": None if hide_last_seen else last_seen_at,
    }


@router.websocket("/ws/stream")
async def stream_websocket(
    websocket: WebSocket,
    token: str = Query(...),
    redis=Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    try:
        user_id = await validate_ws_token(token, redis)
    except Exception as e:
        logger.warning("ws_auth_failed", error=str(e), exc_info=True)
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket_manager.connect(websocket, user_id, redis)
    await db.execute(
        update(User).where(User.id == user_id).values(last_seen_at=datetime.now(timezone.utc))
    )
    await db.commit()

    active_chat_id: str | None = None
    active_peer_id: str | None = None

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=RECEIVE_TIMEOUT
                )
            except asyncio.TimeoutError:
                break
            except Exception as e:
                logger.warning("ws_receive_failed", user_id=user_id, error=str(e), exc_info=True)
                break

            try:
                data = json.loads(raw)
                from app.schemas.message import WsInbound
                msg = WsInbound.model_validate(data)
            except Exception as e:
                logger.warning("ws_invalid_message", user_id=user_id, error=str(e), exc_info=True)
                try:
                    await websocket.send_text(json.dumps({"type": "error", "reason": "bad_message"}))
                except Exception:
                    pass
                continue
            msg_type = msg.type

            if msg_type == "ping":
                await websocket_manager.heartbeat(user_id, redis)
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "subscribe":
                chat_id = msg.chat_id
                resolved = await _resolve_chat(db, str(chat_id), user_id) if chat_id else None
                if resolved:
                    channel, other_user_id = resolved
                    await websocket_manager.subscribe(
                        user_id, channel, other_user_id, redis
                    )
                    active_chat_id = channel
                    active_peer_id = other_user_id
                    snapshot = await _presence_snapshot(
                        db, other_user_id, redis, channel
                    )
                    try:
                        await websocket.send_text(json.dumps(snapshot))
                    except Exception as e:
                        logger.warning("ws_snapshot_send_failed", user_id=user_id, error=str(e), exc_info=True)
                        break
                    logger.info(
                        "WS subscribed", user_id=user_id, chat_id=channel
                    )

            elif msg_type == "unsubscribe":
                chat_id = msg.chat_id
                if chat_id:
                    await websocket_manager.unsubscribe(user_id, str(chat_id))
                    if active_chat_id == str(chat_id):
                        active_chat_id = None
                        active_peer_id = None
                    logger.info(
                        "WS unsubscribed", user_id=user_id, chat_id=chat_id
                    )

            elif msg_type == "typing":
                if active_chat_id and active_peer_id:
                    await websocket_manager.set_typing(
                        active_chat_id, user_id, redis, active_peer_id
                    )

            elif msg_type == "typing_stopped":
                if active_chat_id and active_peer_id:
                    await websocket_manager.clear_typing(
                        active_chat_id, user_id, redis, active_peer_id
                    )

            elif msg_type == "read":
                message_ids = [str(x) for x in (msg.message_ids or [])]
                if active_chat_id and active_peer_id:
                    await websocket_manager.send_to_conversation(
                        channel=active_chat_id,
                        sender_id=user_id,
                        message={
                            "type": "messages_read",
                            "chat_id": active_chat_id,
                            "message_ids": message_ids,
                            "reader_id": user_id,
                        },
                        other_user_id=active_peer_id,
                        redis=redis,
                    )

            # Close the read transaction opened by this message so the pooled
            # DB connection is released back to pgbouncer between messages.
            await db.commit()

    except asyncio.CancelledError:
        # Graceful shutdown (SIGTERM): tell the client we're draining, then
        # re-raise so uvicorn's CancelledError propagates normally.
        try:
            await websocket.send_text(json.dumps({"type": "server_shutdown"}))
        except Exception:
            pass
        raise
    except Exception:
        logger.exception("WS stream error", user_id=user_id)
    finally:
        last_seen_at = datetime.now(timezone.utc)
        hide_last_seen = False
        try:
            ts = await db.scalar(
                select(User.last_seen_at).where(User.id == user_id)
            )
            if ts:
                last_seen_at = ts
            settings = await db.scalar(
                select(UserSettings.hide_last_seen).where(
                    UserSettings.user_id == user_id
                )
            )
            hide_last_seen = bool(settings)
        except Exception as e:
            logger.warning("ws_presence_read_failed", user_id=user_id, error=str(e), exc_info=True)
        await websocket_manager.broadcast_peer_presence(
            user_id,
            {
                "type": "user_offline",
                "user_id": user_id,
                "last_seen_at": None if hide_last_seen else last_seen_at.isoformat(),
            },
            redis,
        )
        await websocket_manager.disconnect(websocket, user_id, redis)
        await db.execute(
            update(User).where(User.id == user_id).values(last_seen_at=datetime.now(timezone.utc))
        )
        await db.commit()