import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.services.websocket_manager import websocket_manager
from app.core.deps import validate_ws_token
from app.core.redis import get_redis
from app.db.session import get_db
from app.models.match import Match
from app.models.user import User
from app.models.block import Block
from app.core.logging import get_logger

logger = get_logger("websocket")

router = APIRouter()


@router.websocket("/ws/chat/{identifier}")
async def chat_websocket(
    websocket: WebSocket,
    identifier: str,
    token: str = Query(...),
    redis=Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    try:
        user_id = await validate_ws_token(token, redis)
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Try to resolve the identifier as an active match the user belongs to.
    match_result = await db.execute(
        select(Match).where(
            Match.id == identifier,
            Match.is_active == True,
            or_(
                Match.user1_id == user_id,
                Match.user2_id == user_id,
            ),
        )
    )
    match_obj = match_result.scalar_one_or_none()

    if match_obj:
        # ── Matched chat ──────────────────────────────────────────────
        other_user_id = (
            str(match_obj.user2_id) if str(match_obj.user1_id) == user_id
            else str(match_obj.user1_id)
        )
        channel = websocket_manager.conversation_channel(
            str(match_obj.id), user_id, None
        )
    else:
        # ── Unmatched chat ── identifier is the other user's id ───────
        other_user_id = identifier

        if other_user_id == user_id:
            await websocket.close(code=4003, reason="Access denied")
            return

        target_result = await db.execute(
            select(User).where(User.id == other_user_id, User.is_active == True)
        )
        target_user = target_result.scalar_one_or_none()
        if not target_user:
            await websocket.close(code=4003, reason="Access denied")
            return

        blocked = await db.scalar(
            select(Block.id).where(
                or_(
                    (Block.blocker_id == user_id)
                    & (Block.blocked_id == other_user_id),
                    (Block.blocker_id == other_user_id)
                    & (Block.blocked_id == user_id),
                )
            ).limit(1)
        )
        if blocked:
            await websocket.close(code=4003, reason="Access denied")
            return

        channel = websocket_manager.conversation_channel(None, user_id, other_user_id)

    await websocket_manager.add_chat_connection(websocket, channel, user_id, redis)

    await websocket_manager.send_to_conversation(
        channel=channel,
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
                    await websocket_manager.heartbeat(user_id, redis)
                    await websocket.send_text(json.dumps({"type": "pong"}))

                elif msg_type == "typing":
                    await websocket_manager.set_typing(channel, user_id, redis)

                elif msg_type == "typing_stopped":
                    await websocket_manager.clear_typing(channel, user_id, redis)

                elif msg_type == "read":
                    message_ids = data.get("message_ids", [])
                    await websocket_manager.send_to_conversation(
                        channel=channel,
                        sender_id=user_id,
                        message={
                            "type": "messages_read",
                            "message_ids": message_ids,
                            "reader_id": user_id,
                        },
                        other_user_id=other_user_id,
                        redis=redis,
                    )

            except asyncio.TimeoutError:
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket_manager.send_to_conversation(
            channel=channel,
            sender_id=user_id,
            message={"type": "user_offline", "user_id": user_id},
            other_user_id=other_user_id,
            redis=redis,
        )
        await websocket_manager.clear_typing(channel, user_id, redis)
        await websocket_manager.remove_chat_connection(websocket, channel, user_id, redis)