# app/api/v1/endpoints/messages.py
import uuid
from typing import Optional, Tuple
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from uuid import UUID
from datetime import datetime, timezone

from app.db.session import get_session
from app.models.user import User
from app.models.chat import Chat
from app.models.message import Message
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.schemas.message import (
    MessageResponse, MessageListResponse, TextMessageRequest,
    SendMessageResponse, ForwardMessageRequest,
    MarkReadRequest, MessageStatusResponse,
    MessageActionResponse, ForwardMessageResponse,
)
from app.services.chat_service import (
    get_user_chat, can_send_message,
    mark_messages_as_delivered, mark_messages_as_read,
    delete_message, forward_message, create_encrypted_message,
    get_decrypted_message_for_client,
)
from app.services.media_service import MediaService
from app.services.notification_service import NotificationService
from app.services.websocket_manager import websocket_manager
import app.core.redis as redis
from app.core.redis import redis_client

from app.core.logging import get_logger

logger = get_logger("messages")

router = APIRouter(prefix="/messages", tags=["messages"])


async def get_chat_or_404(
    session: AsyncSession, chat_id: UUID, user_id: UUID
) -> Tuple[Chat, UUID]:
    """Fetch an active chat the user belongs to. Returns (chat, other_user_id)."""
    chat = await get_user_chat(session, chat_id, user_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    other_user_id = chat.recipient_id if chat.initiator_id == user_id else chat.initiator_id
    return chat, other_user_id


async def _background_websocket_send(
    channel: str,
    sender_id_str: str,
    message_data: dict,
    other_user_id_str: str,
    redis_client,
):
    await websocket_manager.send_to_conversation(
        channel,
        sender_id_str,
        message_data,
        other_user_id_str,
        redis_client,
    )


async def _background_chat_updated(
    session: AsyncSession,
    recipient_user_id: UUID,
    chat_id: UUID,
    status: str,
    last_message: dict,
    updated_at: Optional[datetime],
):
    """Publish a chat_updated event on the recipient's personal channel so
    their chat list can reorder and refresh in real time."""
    unread = await session.scalar(
        select(func.count()).select_from(Message).where(
            Message.chat_id == chat_id,
            Message.receiver_id == recipient_user_id,
            Message.is_read == False,
            Message.is_deleted_for_all == False,
            Message.is_deleted_for_receiver == False,
        )
    ) or 0
    await websocket_manager.send_personal_message(
        str(recipient_user_id),
        {
            "type": "chat_updated",
            "data": {
                "chat_id": str(chat_id),
                "status": status,
                "unread_count": unread,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "last_message": last_message,
            },
        },
        redis_client,
    )


def _build_message_response(
    msg: Message,
    decrypted_content: Optional[str] = None,
    reply_to_data: Optional[dict] = None,
) -> MessageResponse:
    """Build a full MessageResponse from a Message row."""
    from app.schemas.message import ReplyToResponse
    reply = None
    if reply_to_data:
        reply = ReplyToResponse(**reply_to_data)
    return MessageResponse(
        id=msg.id,
        chat_id=msg.chat_id,
        sender_id=msg.sender_id,
        receiver_id=msg.receiver_id,
        message_type=msg.message_type,
        content=decrypted_content if decrypted_content is not None else msg.content,
        media_url=msg.media_url,
        media_duration=msg.media_duration,
        reply_to=reply,
        is_sent=msg.is_sent,
        is_delivered=msg.is_delivered,
        is_read=msg.is_read,
        sent_at=msg.sent_at,
        delivered_at=msg.delivered_at,
        read_at=msg.read_at,
    )


@router.get("/{chat_id}", response_model=MessageListResponse)
@limiter.limit("60/minute")
async def get_chat_history(
    request: Request,
    chat_id: UUID,
    limit: int = Query(30, ge=1, le=50),
    offset: int = Query(0, ge=0),
    before: Optional[datetime] = Query(None, description="Cursor: get messages older than this timestamp (ISO format)"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageListResponse:
    """
    Get chat history for a chat.

    Pagination:
      - Legacy: use `offset` + `limit`
      - Cursor:  use `before` (ISO datetime) + `limit`
                 Client passes the `sent_at` of the oldest loaded message as `before` for the next page.
    """
    await get_chat_or_404(session, chat_id, current_user.id)

    query = select(Message).where(
        Message.chat_id == chat_id,
        Message.is_deleted_for_all == False,
        or_(
            Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id,
        ),
    )
    query = query.where(
        or_(
            Message.is_deleted_for_sender == False,
            Message.sender_id != current_user.id,
        )
    )
    query = query.where(
        or_(
            Message.is_deleted_for_receiver == False,
            Message.receiver_id != current_user.id,
        )
    )

    if before:
        if before.tzinfo is None:
            before = before.replace(tzinfo=timezone.utc)
        query = query.where(Message.sent_at < before)

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.order_by(Message.sent_at.desc())
    if not before:
        query = query.offset(offset)
    query = query.limit(limit)
    result = await session.execute(query)
    messages = result.scalars().all()

    message_responses = []
    for msg in reversed(messages):
        decrypted_data = await get_decrypted_message_for_client(session, msg, current_user.id)

        reply_to_data = None
        if msg.reply_to_id:
            reply_result = await session.execute(
                select(Message).where(Message.id == msg.reply_to_id)
            )
            reply_msg = reply_result.scalar_one_or_none()
            if reply_msg:
                reply_content = reply_msg.content
                reply_to_data = {
                    "id": reply_msg.id,
                    "content": reply_content[:100] if reply_content else "[Media]",
                    "sender_name": "You" if reply_msg.sender_id == current_user.id else "Them",
                    "message_type": reply_msg.message_type,
                }

        message_responses.append(MessageResponse(
            id=msg.id,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            receiver_id=msg.receiver_id,
            message_type=msg.message_type,
            content=decrypted_data.get("content"),
            media_url=msg.media_url,
            media_duration=msg.media_duration,
            reply_to=reply_to_data,
            is_sent=msg.is_sent,
            is_delivered=msg.is_delivered,
            is_read=msg.is_read,
            sent_at=msg.sent_at,
            delivered_at=msg.delivered_at,
            read_at=msg.read_at,
        ))

    has_more = len(messages) == limit
    if before:
        next_offset = None
    else:
        next_offset = offset + limit if offset + limit < total else None

    return MessageListResponse(
        messages=message_responses,
        total=total or 0,
        next_offset=next_offset,
    )


@router.post("/{chat_id}/text", response_model=SendMessageResponse)
@limiter.limit("60/minute")
async def send_text_message(
    request: Request,
    chat_id: UUID,
    body: TextMessageRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a text message in a chat."""
    chat, other_user_id = await get_chat_or_404(session, chat_id, current_user.id)

    can_send, reason = await can_send_message(session, chat, current_user.id)
    if not can_send:
        raise HTTPException(status_code=403, detail=reason)

    # Per-chat message rate limit (30/min per sender per chat)
    rate_key = f"msg_rate:{current_user.id}:{chat.id}"
    try:
        pipe = redis.redis_client.pipeline()
        incr_result = pipe.incr(rate_key)
        pipe.expire(rate_key, 60)
        results = await pipe.execute()
        count = results[0]
        if count > 30:
            raise HTTPException(status_code=429, detail="Sending too fast. Please slow down.")
    except HTTPException:
        raise
    except Exception:
        logger.warning("Redis rate limit check failed, allowing message")

    new_message = await create_encrypted_message(
        session=session,
        chat_id=chat.id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        content=body.content,
        message_type="text",
        reply_to_id=body.reply_to_id,
    )
    chat.updated_at = func.now()

    notification_service = NotificationService(session)
    sender_name = current_user.profile.name if current_user.profile else "Someone"
    await notification_service.notify_message(
        receiver_id=other_user_id,
        sender_name=sender_name,
        chat_id=chat.id,
    )

    await session.commit()

    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "chat_id": str(chat.id),
            "message_type": "text",
            "content": body.content,
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
    }
    background_tasks.add_task(
        _background_websocket_send,
        channel=websocket_manager.conversation_channel(str(chat.id)),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )
    background_tasks.add_task(
        _background_chat_updated,
        session=session,
        recipient_user_id=other_user_id,
        chat_id=chat.id,
        status=chat.status,
        last_message={
            "content": body.content,
            "message_type": "text",
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
        updated_at=new_message.sent_at,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=chat.status == "pending",
        chat_accepted=chat.status == "accepted",
        chats_remaining_today=None,
        message=_build_message_response(new_message, decrypted_content=body.content),
    )


@router.post("/{chat_id}/photo", response_model=SendMessageResponse)
@limiter.limit("30/minute")
async def send_photo_message(
    request: Request,
    chat_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    caption: str = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a photo message (only in accepted chats)."""
    chat, other_user_id = await get_chat_or_404(session, chat_id, current_user.id)

    if chat.status != "accepted":
        raise HTTPException(status_code=403, detail="Photos can only be sent in accepted chats")

    file_data = await file.read()
    message_id = uuid.uuid4()

    success, media_url, error = await MediaService.save_photo(
        file_data, str(chat.id), str(message_id)
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    new_message = await create_encrypted_message(
        session=session,
        chat_id=chat.id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        content=caption or "",
        message_type="photo",
        media_url=media_url,
    )
    chat.updated_at = func.now()
    await session.commit()

    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "chat_id": str(chat.id),
            "message_type": "photo",
            "media_url": media_url,
            "caption": caption or "",
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
    }
    background_tasks.add_task(
        _background_websocket_send,
        channel=websocket_manager.conversation_channel(str(chat.id)),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )
    background_tasks.add_task(
        _background_chat_updated,
        session=session,
        recipient_user_id=other_user_id,
        chat_id=chat.id,
        status=chat.status,
        last_message={
            "content": caption or "",
            "message_type": "photo",
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
        updated_at=new_message.sent_at,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=False,
        chat_accepted=True,
        message=_build_message_response(new_message, decrypted_content=caption or ""),
    )


@router.post("/{chat_id}/voice", response_model=SendMessageResponse)
@limiter.limit("30/minute")
async def send_voice_message(
    request: Request,
    chat_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    duration: int = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a voice message (only in accepted chats)."""
    chat, other_user_id = await get_chat_or_404(session, chat_id, current_user.id)

    if chat.status != "accepted":
        raise HTTPException(status_code=403, detail="Voice messages can only be sent in accepted chats")

    file_data = await file.read()
    message_id = uuid.uuid4()

    success, media_url, error = await MediaService.save_voice(
        file_data, str(chat.id), str(message_id), duration
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    new_message = await create_encrypted_message(
        session=session,
        chat_id=chat.id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        content="",
        message_type="voice",
        media_url=media_url,
        media_duration=duration,
    )
    chat.updated_at = func.now()
    await session.commit()

    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "chat_id": str(chat.id),
            "message_type": "voice",
            "media_url": media_url,
            "duration": duration,
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
    }
    background_tasks.add_task(
        _background_websocket_send,
        channel=websocket_manager.conversation_channel(str(chat.id)),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )
    background_tasks.add_task(
        _background_chat_updated,
        session=session,
        recipient_user_id=other_user_id,
        chat_id=chat.id,
        status=chat.status,
        last_message={
            "content": "",
            "message_type": "voice",
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
        },
        updated_at=new_message.sent_at,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=False,
        chat_accepted=True,
        message=_build_message_response(new_message, decrypted_content=None),
    )


@router.post("/delivered", response_model=MessageActionResponse)
@limiter.limit("100/minute")
async def mark_delivered(
    request: Request,
    body: MarkReadRequest,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Mark messages as delivered"""
    await mark_messages_as_delivered(session, body.message_ids, current_user_id)
    return {"message": f"{len(body.message_ids)} messages marked as delivered"}


@router.post("/read", response_model=MessageActionResponse)
@limiter.limit("100/minute")
async def mark_read(
    request: Request,
    body: MarkReadRequest,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Mark messages as read"""
    await mark_messages_as_read(session, body.message_ids, current_user_id)
    return {"message": f"{len(body.message_ids)} messages marked as read"}


@router.delete("/{message_id}", response_model=MessageActionResponse)
@limiter.limit("30/minute")
async def delete_message_endpoint(
    request: Request,
    message_id: UUID,
    delete_for: str = "me",
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """Delete a message (for me or for everyone)"""
    success, error = await delete_message(session, message_id, current_user_id, delete_for)
    if not success:
        raise HTTPException(status_code=400, detail=error)
    return {"message": f"Message deleted for {delete_for}"}


@router.post("/{message_id}/forward", response_model=ForwardMessageResponse)
@limiter.limit("30/minute")
async def forward_message_endpoint(
    request: Request,
    message_id: UUID,
    body: ForwardMessageRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Forward a message to another chat"""
    new_message, error = await forward_message(session, message_id, body.target_chat_id, current_user.id)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"message": "Message forwarded", "new_message_id": str(new_message.id)}


@router.get("/{message_id}/status", response_model=MessageStatusResponse)
@limiter.limit("60/minute")
async def get_message_status(
    request: Request,
    message_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageStatusResponse:
    """Get delivery and read status of a message"""
    result = await session.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view status of this message")

    return MessageStatusResponse(
        id=message.id,
        sent_at=message.sent_at,
        delivered_at=message.delivered_at,
        read_at=message.read_at,
        is_delivered=message.is_delivered,
        is_read=message.is_read,
    )
