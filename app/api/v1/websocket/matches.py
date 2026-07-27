import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from app.services.websocket_manager import websocket_manager, HEARTBEAT_INTERVAL
from app.core.deps import validate_ws_token
from app.core.redis import get_redis
from app.core.logging import get_logger

logger = get_logger("websocket")

router = APIRouter()


@router.websocket("/ws/matches")
async def matches_websocket(
    websocket: WebSocket,
    token: str = Query(...),
    redis=Depends(get_redis),
):
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
                    await websocket_manager.heartbeat(user_id, redis)
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except asyncio.TimeoutError:
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await websocket_manager.disconnect(websocket, user_id, redis)