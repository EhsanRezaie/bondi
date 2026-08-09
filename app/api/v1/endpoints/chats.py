# app/api/v1/endpoints/chats.py
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Query, Response
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional

from app.db.session import get_session
from app.models.user import User
from app.models.chat import Chat
from app.models.message import Message
from app.models.block import Block
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.config import settings
from app.schemas.chat import (
    ChatCreateRequest,
    ChatCreateResponse,
    ChatAcceptResponse,
    ChatListResponse,
    ChatItemResponse,
    ChatDetailResponse,
    ChatUserResponse,
    ChatLastMessage,
)
from app.services.chat_service import (
    find_chat_for_pair,
    get_user_chat,
    create_chat_for_pair,
    have_mutual_like,
    can_start_new_chat,
    consume_new_chat,
    create_encrypted_message,
    get_last_messages_for_chats,
    chat_is_ended,
)
from app.services.notification_service import NotificationService
from app.services.reward_service import RewardService
from app.services.photo_service import PhotoService
from app.services.websocket_manager import websocket_manager
import app.core.redis as redis_module

from app.core.logging import get_logger

logger = get_logger("chats")

router = APIRouter(prefix="/chats", tags=["chats"])


async def _main_photo_url(session: AsyncSession, photo) -> Optional[str]:
    if not photo:
        return None
    return await PhotoService.get_photo_url(photo.url, photo.status)


async def _load_user_with_media(session: AsyncSession, user_id: UUID) -> Optional[User]:
    if not user_id:
        return None
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
            selectinload(User.settings),
        )
        .where(User.id == user_id, User.is_active == True)
    )
    return result.scalar_one_or_none()


async def _background_websocket_send(
    channel: str,
    sender_id_str: str,
    message_data: dict,
    other_user_id_str: str,
    redis: "Any",
):
    try:
        await websocket_manager.send_to_conversation(
            channel,
            sender_id_str,
            message_data,
            other_user_id_str,
            redis,
        )
    except Exception as e:
        logger.error("bg_websocket_send_failed", channel=channel, error=str(e), exc_info=True)


async def _background_personal_send(
    user_id: str,
    message: dict,
    redis: "Any",
):
    try:
        await websocket_manager.send_personal_message(user_id, message, redis)
    except Exception as e:
        logger.error("bg_personal_send_failed", user_id=user_id, error=str(e), exc_info=True)


@router.post("", response_model=ChatCreateResponse)
@limiter.limit("20/minute")
async def create_chat(
    request: Request,
    body: ChatCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatCreateResponse:
    """
    Start a chat with another user (pure chat — no like is recorded).
    - Existing chat for the pair → returns it (is_new=false), no message, no limit.
    - New chat → creates the chat (accepted if the pair already has a mutual
      like, else pending), sends the first message, and consumes one daily
      chat slot for non-premium users (429 if the daily limit is reached).
    """
    user_id = current_user.id
    target_user_id = body.user_id

    if target_user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot chat with yourself")

    target_user = await _load_user_with_media(session, target_user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # ── Blocked users (either direction) ─────────────────────────────
    blocked = await session.scalar(
        select(Block.id).where(
            or_(
                (Block.blocker_id == user_id) & (Block.blocked_id == target_user_id),
                (Block.blocker_id == target_user_id) & (Block.blocked_id == user_id),
            )
        ).limit(1)
    )
    if blocked:
        raise HTTPException(status_code=403, detail="You cannot start a chat with this user")

    # ── Existing chat for the pair ────────────────────────────────────
    existing = await find_chat_for_pair(session, user_id, target_user_id)
    if existing:
        return ChatCreateResponse(
            chat_id=existing.id,
            is_new=False,
            status=existing.status,
            message=None,
            created_at=existing.created_at,
        )

    # ── Per-pair open cap: guard against mass opening chats with the same user ──
    pair_key = f"chat_open:{user_id}:{target_user_id}"
    try:
        pipe = redis_module.redis_client.pipeline()
        pipe.incr(pair_key)
        pipe.expire(pair_key, 86400)
        results = await pipe.execute()
        if results[0] > 3:
            raise HTTPException(
                status_code=429,
                detail="You have opened too many conversations with this user. Please like them from discover instead.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("chat_open_limit_check_failed", error=str(e), exc_info=True)

    # ── New chat: daily limit ─────────────────────────────────────────
    is_premium = bool(current_user.profile and current_user.profile.is_premium)
    can_start, reason = await can_start_new_chat(session, user_id, is_premium)
    if not can_start:
        raise HTTPException(status_code=429, detail=reason)

    # ── Create chat (auto-accepted when the pair already has a mutual like) ──
    mutual_like = await have_mutual_like(session, user_id, target_user_id)
    status = "accepted" if mutual_like else "pending"

    chat = await create_chat_for_pair(session, user_id, target_user_id, status)

    # ── First message ─────────────────────────────────────────────────
    new_message = await create_encrypted_message(
        session=session,
        chat_id=chat.id,
        sender_id=user_id,
        receiver_id=target_user_id,
        content=body.content,
        message_type="text",
    )
    chat.updated_at = func.now()

    # ── Consume one daily chat slot (no-op for premium) ───────────────
    consumed = await consume_new_chat(session, user_id, is_premium)
    if not consumed:
        await session.rollback()
        raise HTTPException(
            status_code=429,
            detail=f"Daily new chat limit reached ({settings.FREE_USER_DAILY_CHATS} per day). Watch an ad or upgrade to premium.",
        )
    await session.commit()

    # ── Notifications + realtime ──────────────────────────────────────
    sender_name = current_user.profile.name if current_user.profile else "Someone"
    notification_service = NotificationService(session)
    await notification_service.notify_message(
        receiver_id=target_user_id,
        sender_name=sender_name,
        chat_id=chat.id,
    )
    await session.commit()

    channel = websocket_manager.conversation_channel(str(chat.id))
    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "chat_id": str(chat.id),
            "message_type": "text",
            "content": body.content,
            "sender_id": str(user_id),
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
    }
    background_tasks.add_task(
        _background_websocket_send,
        channel=channel,
        sender_id_str=str(user_id),
        message_data=message_data,
        other_user_id_str=str(target_user_id),
        redis=redis_module.redis_client,
    )
    # Notify the other user's chat list (even if they are not in the chat channel)
    await websocket_manager.send_personal_message(
        str(target_user_id),
        {
            "type": "new_chat",
            "data": {
                "chat_id": str(chat.id),
                "status": status,
                "initiator_id": str(user_id),
            },
        },
        redis_module.redis_client,
    )

    chats_remaining = None
    if not is_premium:
        reward_service = RewardService(session)
        remaining = await reward_service.get_remaining_chats(current_user)
        chats_remaining = remaining if remaining != -1 else None

    return ChatCreateResponse(
        chat_id=chat.id,
        is_new=True,
        status=status,
        message={
            "id": str(new_message.id),
            "content": body.content,
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
        chats_remaining_today=chats_remaining,
        created_at=chat.created_at,
    )


@router.post("/{chat_id}/accept", response_model=ChatAcceptResponse)
@limiter.limit("20/minute")
async def accept_chat(
    request: Request,
    chat_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatAcceptResponse:
    """Accept a pending chat (only the recipient can accept). Unlocks unlimited messages."""
    chat = await get_user_chat(session, chat_id, current_user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if current_user.id != chat.recipient_id:
        raise HTTPException(status_code=403, detail="Only the recipient can accept this chat")

    if chat.status == "accepted":
        return ChatAcceptResponse(
            chat_id=chat.id,
            status="accepted",
            message="Chat already accepted.",
        )

    chat.status = "accepted"
    await session.commit()

    other_user_id = chat.initiator_id if chat.recipient_id == current_user.id else chat.recipient_id
    await websocket_manager.send_to_conversation(
        channel=websocket_manager.conversation_channel(str(chat.id)),
        sender_id=str(current_user.id),
        message={
            "type": "chat_accepted",
            "data": {
                "chat_id": str(chat.id),
                "accepted_by": str(current_user.id),
                "status": "accepted",
            },
        },
        other_user_id=str(other_user_id),
        redis=redis_module.redis_client,
    )

    # Notify both users' personal channels so their chat lists re-bucket in real time.
    for uid in (str(chat.initiator_id), str(chat.recipient_id)):
        await websocket_manager.send_personal_message(
            uid,
            {
                "type": "chat_accepted",
                "data": {
                    "chat_id": str(chat.id),
                    "accepted_by": str(current_user.id),
                    "status": "accepted",
                },
            },
            redis_module.redis_client,
        )

    return ChatAcceptResponse(
        chat_id=chat.id,
        status="accepted",
        message="Chat accepted. You can now send unlimited messages.",
    )


@router.get("", response_model=ChatListResponse)
@limiter.limit("60/minute")
async def list_chats(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, pattern="^(accepted|pending)$"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatListResponse:
    """List all chats for the current user, sorted by latest activity."""
    user_id = current_user.id

    blocked_ids = set(
        (await session.execute(select(Block.blocked_id).where(Block.blocker_id == user_id))).scalars().all()
    ) | set(
        (await session.execute(select(Block.blocker_id).where(Block.blocked_id == user_id))).scalars().all()
    )

    filters = [
        or_(Chat.initiator_id == user_id, Chat.recipient_id == user_id),
        Chat.is_active == True,
    ]
    if status:
        filters.append(Chat.status == status)

    result = await session.execute(
        select(Chat)
        .options(
            selectinload(Chat.initiator).selectinload(User.profile),
            selectinload(Chat.initiator).selectinload(User.photos),
            selectinload(Chat.initiator).selectinload(User.settings),
            selectinload(Chat.recipient).selectinload(User.profile),
            selectinload(Chat.recipient).selectinload(User.photos),
            selectinload(Chat.recipient).selectinload(User.settings),
        )
        .where(*filters)
        .order_by(Chat.updated_at.desc())
    )
    chats = result.scalars().all()

    last_messages = await get_last_messages_for_chats(
        session, [c.id for c in chats]
    )

    rows = []
    for chat in chats:
        if user_id == chat.initiator_id and chat.deleted_for_initiator:
            continue
        if user_id == chat.recipient_id and chat.deleted_for_recipient:
            continue

        other = chat.recipient if chat.initiator_id == user_id else chat.initiator
        if other is None:
            continue

        # Blocked chats are NOT hidden anymore. They stay in the list but are
        # flagged so the client can show "This conversation is over."
        is_blocked = other.id in blocked_ids
        is_ended = await chat_is_ended(session, chat, user_id)

        unread = await session.scalar(
            select(func.count()).select_from(Message).where(
                Message.chat_id == chat.id,
                Message.receiver_id == user_id,
                Message.is_read == False,
                Message.is_deleted_for_all == False,
                Message.is_deleted_for_receiver == False,
            )
        ) or 0

        last_msg = last_messages.get(chat.id)
        last_message = None
        updated_at = chat.updated_at
        if last_msg is not None:
            last_message = ChatLastMessage(
                content=last_msg.content,
                message_type=last_msg.message_type,
                is_sent=last_msg.sender_id == user_id,
                is_read=last_msg.is_read,
                sent_at=last_msg.sent_at,
            )
            updated_at = last_msg.sent_at or chat.updated_at

        main_photo = (
            next((p for p in other.photos if p.is_main and p.status == "approved"), None)
            if other.photos
            else None
        )
        main_photo_url = await _main_photo_url(session, main_photo)

        rows.append({
            "chat_id": chat.id,
            "status": chat.status,
            "initiator_id": chat.initiator_id,
            "other": other,
            "main_photo_url": main_photo_url,
            "last_message": last_message,
            "unread_count": unread,
            "updated_at": updated_at,
            "is_blocked": is_blocked,
            "is_ended": is_ended,
        })

    rows.sort(key=lambda r: r["updated_at"] or r["chat_id"], reverse=True)
    total = len(rows)
    page = rows[offset: offset + limit]

    # ── Presence (online) for the page ────────────────────────────────
    page_user_ids = [r["other"].id for r in page]
    online_map = {}
    if page_user_ids:
        online_map = await websocket_manager.get_online_status_bulk(
            [str(u) for u in page_user_ids], redis_module.redis_client
        )

    chats_out = []
    for r in page:
        other = r["other"]
        hide_last_seen = bool(other.settings and other.settings.hide_last_seen)
        chats_out.append(
            ChatItemResponse(
                id=r["chat_id"],
                status=r["status"],
                initiator_id=r["initiator_id"],
                user=ChatUserResponse(
                    id=other.id,
                    name=other.profile.name if other.profile else "User",
                    age=other.profile.age if other.profile else 0,
                    main_photo_url=r["main_photo_url"],
                    is_online=online_map.get(str(other.id), False),
                    last_seen_at=None if hide_last_seen else other.last_seen_at,
                ),
                last_message=r["last_message"],
                unread_count=r["unread_count"],
                updated_at=r["updated_at"],
                is_blocked=r["is_blocked"],
                is_ended=r["is_ended"],
            )
        )

    next_offset = offset + limit if offset + limit < total else None

    return ChatListResponse(
        chats=chats_out,
        total=total,
        next_offset=next_offset,
    )


@router.get("/{chat_id}", response_model=ChatDetailResponse)
@limiter.limit("60/minute")
async def get_chat_detail(
    request: Request,
    chat_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ChatDetailResponse:
    """Get a single chat's metadata (status + other user info)."""
    chat = await get_user_chat(session, chat_id, current_user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if current_user.id == chat.initiator_id and chat.deleted_for_initiator:
        raise HTTPException(status_code=403, detail="You have deleted this chat")
    if current_user.id == chat.recipient_id and chat.deleted_for_recipient:
        raise HTTPException(status_code=403, detail="You have deleted this chat")

    other_id = chat.recipient_id if chat.initiator_id == current_user.id else chat.initiator_id
    other = await _load_user_with_media(session, other_id)
    if other is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    online_map = await websocket_manager.get_online_status_bulk(
        [str(other.id)], redis_module.redis_client
    )
    main_photo = (
        next((p for p in other.photos if p.is_main and p.status == "approved"), None)
        if other.photos
        else None
    )
    main_photo_url = await _main_photo_url(session, main_photo)

    return ChatDetailResponse(
        id=chat.id,
        status=chat.status,
        initiator_id=chat.initiator_id,
        recipient_id=chat.recipient_id,
        user=ChatUserResponse(
            id=other.id,
            name=other.profile.name if other.profile else "User",
            age=other.profile.age if other.profile else 0,
            main_photo_url=main_photo_url,
            is_online=online_map.get(str(other.id), False),
            last_seen_at=(
                None
                if (other.settings and other.settings.hide_last_seen)
                else other.last_seen_at
            ),
        ),
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        is_blocked=await chat_is_ended(session, chat, current_user.id),
        is_ended=chat.is_ended,
    )


@router.delete("/{chat_id}", status_code=204, response_class=Response)
@limiter.limit("20/minute")
async def delete_chat(
    request: Request,
    chat_id: UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """End a conversation and remove it from my chat list.

    For the requesting user the chat disappears from their list; for the other
    participant it becomes "This conversation is over." (is_ended)."""
    chat = await get_user_chat(session, chat_id, current_user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if current_user.id == chat.initiator_id:
        chat.deleted_for_initiator = True
    elif current_user.id == chat.recipient_id:
        chat.deleted_for_recipient = True
    else:
        raise HTTPException(status_code=403, detail="Not a member of this chat")

    if not chat.is_ended:
        chat.is_ended = True
        chat.ended_by = current_user.id
        chat.ended_at = func.now()

    other_user_id = (
        chat.recipient_id if chat.initiator_id == current_user.id
        else chat.initiator_id
    )

    await session.commit()

    # Notify the other participant so their open chat flips to "conversation is over".
    background_tasks.add_task(
        _background_personal_send,
        user_id=str(other_user_id),
        message={"type": "chat_ended", "data": {"chat_id": str(chat.id)}},
        redis=redis_module.redis_client,
    )
    return None