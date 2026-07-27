# app/api/v1/endpoints/messages.py
from typing import Optional, Tuple
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from uuid import UUID
from datetime import datetime, timezone

from app.db.session import get_session
from app.models.user import User
from app.models.match import Match
from app.models.message import Message
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.schemas.message import (
    MessageResponse, MessageListResponse, TextMessageRequest,
    SendMessageResponse, DeleteMessageRequest, ForwardMessageRequest,
    MarkReadRequest, MessageStatusResponse, AcceptChatResponse,
    MessageActionResponse, ForwardMessageResponse,
)
from app.services.chat_service import (
    can_start_new_chat, check_unmatched_message_limit, accept_unmatched_chat,
    increment_new_chat_count, mark_messages_as_delivered, mark_messages_as_read,
    delete_message, forward_message, create_encrypted_message,
    get_decrypted_message_for_client, get_message_for_admin
)
from app.services.media_service import MediaService
from app.services.notification_service import NotificationService
from app.services.reward_service import RewardService
from app.services.websocket_manager import websocket_manager
import app.core.redis as redis
from app.core.redis import redis_client

from app.core.logging import get_logger

logger = get_logger("messages")

router = APIRouter(prefix="/messages", tags=["messages"])


async def get_match_or_chat(session: AsyncSession, identifier: UUID, user_id: UUID) -> Tuple[Optional[Match], Optional[UUID], Optional[UUID]]:
    """Get match or determine if it's an unmatched chat"""
    # First, check if identifier is a valid match ID
    result = await session.execute(
        select(Match).where(
            Match.id == identifier,
            Match.is_active == True,
            or_(
                Match.user1_id == user_id,
                Match.user2_id == user_id
            )
        )
    )
    match = result.scalar_one_or_none()

    if match:
        # This is a matched chat - get the other user
        other_user_id = match.user2_id if match.user1_id == user_id else match.user1_id
        return match, other_user_id, match.id

    # Check if identifier is a valid user ID (for unmatched chat)
    result = await session.execute(
        select(User).where(User.id == identifier, User.is_active == True)
    )
    target_user = result.scalar_one_or_none()
    
    if target_user:
        # This is an unmatched chat - identifier is the other user's ID
        return None, identifier, None
    
    return None, None, None


async def _background_websocket_send(match_id_str: str, sender_id_str: str, message_data: dict, other_user_id_str: str, redis_client):
    """Send WebSocket notification after response is sent."""
    await websocket_manager.send_to_match(
        match_id_str,
        sender_id_str,
        message_data,
        other_user_id_str,
        redis_client,
    )


@router.get("/{identifier}", response_model=MessageListResponse)
@limiter.limit("60/minute")
async def get_chat_history(
    request: Request,
    identifier: UUID,
    limit: int = Query(30, ge=1, le=50),
    offset: int = Query(0, ge=0),
    before: Optional[datetime] = Query(None, description="Cursor: get messages older than this timestamp (ISO format)"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MessageListResponse:
    """
    Get chat history for a match or unmatched chat.
    identifier can be match_id OR user_id (for unmatched chat).

    Pagination:
      - Legacy: use `offset` + `limit`
      - Cursor:  use `before` (ISO datetime) + `limit`
                 Client passes the `sent_at` of the oldest loaded message as `before` for the next page.
    """
    match, other_user_id, match_id = await get_match_or_chat(session, identifier, current_user.id)

    if not match and not other_user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Build query
    if match_id:
        query = select(Message).where(
            Message.match_id == match_id,
            or_(
                Message.sender_id == current_user.id,
                Message.receiver_id == current_user.id
            ),
            Message.is_deleted_for_all == False
        )
    else:
        # Unmatched chat
        query = select(Message).where(
            Message.match_id.is_(None),
            or_(
                and_(
                    Message.sender_id == current_user.id,
                    Message.receiver_id == other_user_id
                ),
                and_(
                    Message.sender_id == other_user_id,
                    Message.receiver_id == current_user.id
                )
            ),
            Message.is_deleted_for_all == False
        )

    # Filter out messages deleted for this user
    query = query.where(
        or_(
            Message.is_deleted_for_sender == False,
            Message.sender_id != current_user.id
        )
    )
    query = query.where(
        or_(
            Message.is_deleted_for_receiver == False,
            Message.receiver_id != current_user.id
        )
    )

    # Cursor pagination: filter before the cursor timestamp
    if before:
        if before.tzinfo is None:
            before = before.replace(tzinfo=timezone.utc)
        query = query.where(Message.sent_at < before)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    # Get messages — cursor pagination OR legacy offset
    query = query.order_by(Message.sent_at.desc())
    if not before:
        query = query.offset(offset)
    query = query.limit(limit)
    result = await session.execute(query)
    messages = result.scalars().all()

    # Build response with decrypted content
    message_responses = []
    for msg in reversed(messages):
        # Get decrypted content for client
        decrypted_data = await get_decrypted_message_for_client(
            session, msg, current_user.id
        )
        
        # Get reply_to data if exists
        reply_to_data = None
        if msg.reply_to_id:
            reply_result = await session.execute(
                select(Message).where(Message.id == msg.reply_to_id)
            )
            reply_msg = reply_result.scalar_one_or_none()
            if reply_msg:
                # Decrypt reply content
                reply_content = reply_msg.content if reply_msg.match_id else reply_msg._content
                reply_to_data = {
                    "id": reply_msg.id,
                    "content": reply_content[:100] if reply_content else "[Media]",
                    "sender_name": "You" if reply_msg.sender_id == current_user.id else "Them",
                    "message_type": reply_msg.message_type
                }

        message_responses.append(MessageResponse(
            id=msg.id,
            match_id=msg.match_id,
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
            is_accepted=msg.is_accepted,
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


@router.post("/{identifier}/text", response_model=SendMessageResponse)
@limiter.limit("60/minute")
async def send_text_message(
    request: Request,
    identifier: UUID,
    body: TextMessageRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a text message"""
    match, other_user_id, match_id = await get_match_or_chat(session, identifier, current_user.id)

    if not match and not other_user_id:
        raise HTTPException(status_code=404, detail="User or match not found")

    # Check if this is a new chat
    if not match and not match_id:
        can_start, reason, daily_limit = await can_start_new_chat(
            session, current_user.id, other_user_id, current_user.profile.is_premium
        )
        if not can_start:
            raise HTTPException(status_code=429, detail=reason)

    # Check unmatched message limits
    can_send, reason, is_accepted = await check_unmatched_message_limit(
        session, current_user.id, other_user_id, match_id
    )
    if not can_send:
        raise HTTPException(status_code=403, detail=reason)

    # Per-match message rate limit (30/min per sender per chat)
    rate_key = f"msg_rate:{current_user.id}:{match_id or other_user_id}"
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

    # Create message with encryption
    new_message = await create_encrypted_message(
        session=session,
        match_id=match_id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        content=body.content,
        message_type="text",
        reply_to_id=body.reply_to_id,
        is_accepted=is_accepted or match_id is not None,
    )

    # Send push notification to receiver
    notification_service = NotificationService(session)
    await notification_service.notify_message(
        receiver_id=other_user_id,
        sender_name=current_user.profile.name,
        match_id=match_id or other_user_id,
    )

    await session.flush()

    # Increment new chat counter if this is a new chat
    if not match and not match_id:
        await increment_new_chat_count(session, current_user.id)

    await session.commit()

    # Get updated chat remaining count for the client
    chats_remaining = None
    if not current_user.profile.is_premium:
        reward_service = RewardService(session)
        chats_result = await reward_service.get_remaining_chats(current_user)
        chats_remaining = chats_result if chats_result != -1 else None

    # Offload WebSocket notification to background
    decrypted_content = new_message.content if new_message.match_id else new_message._content
    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "message_type": "text",
            "content": decrypted_content,
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat(),
        }
    }
    background_tasks.add_task(
        _background_websocket_send,
        match_id_str=str(match_id) if match_id else str(other_user_id),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=not is_accepted and not match_id,
        chat_accepted=is_accepted or match_id is not None,
        chats_remaining_today=chats_remaining,
    )


@router.post("/{identifier}/photo", response_model=SendMessageResponse)
@limiter.limit("30/minute")
async def send_photo_message(
    request: Request,
    identifier: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    caption: str = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a photo message"""
    match, other_user_id, match_id = await get_match_or_chat(session, identifier, current_user.id)

    if not match and not other_user_id:
        raise HTTPException(status_code=404, detail="User or match not found")

    # Photos can only be sent in matched chats or accepted unmatched chats
    if not match_id:
        can_send, reason, is_accepted = await check_unmatched_message_limit(
            session, current_user.id, other_user_id, None
        )
        if not can_send or not is_accepted:
            raise HTTPException(status_code=403, detail="Photos can only be sent in accepted chats")

    # Read file
    file_data = await file.read()

    # Generate IDs
    message_id = uuid.uuid4()

    # Save photo
    success, media_url, error = await MediaService.save_photo(
        file_data, str(other_user_id if not match_id else match_id), str(message_id)
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    # Create message with encryption (caption only)
    new_message = await create_encrypted_message(
        session=session,
        match_id=match_id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        content=caption or "",
        message_type="photo",
        is_accepted=True,
        media_url=media_url,
    )
    
    await session.commit()

    # Offload WebSocket notification to background
    decrypted_content = new_message.content if new_message.match_id else new_message._content
    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "message_type": "photo",
            "media_url": media_url,
            "caption": decrypted_content,
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat(),
        }
    }
    background_tasks.add_task(
        _background_websocket_send,
        match_id_str=str(match_id) if match_id else str(other_user_id),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=False,
        chat_accepted=True
    )


@router.post("/{identifier}/voice", response_model=SendMessageResponse)
@limiter.limit("30/minute")
async def send_voice_message(
    request: Request,
    identifier: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    duration: int = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SendMessageResponse:
    """Send a voice message"""
    match, other_user_id, match_id = await get_match_or_chat(session, identifier, current_user.id)

    if not match and not other_user_id:
        raise HTTPException(status_code=404, detail="User or match not found")

    # Voice messages can only be sent in matched chats or accepted unmatched chats
    if not match_id:
        can_send, reason, is_accepted = await check_unmatched_message_limit(
            session, current_user.id, other_user_id, None
        )
        if not can_send or not is_accepted:
            raise HTTPException(status_code=403, detail="Voice messages can only be sent in accepted chats")

    # Read file
    file_data = await file.read()

    # Generate IDs
    message_id = uuid.uuid4()

    # Save voice
    success, media_url, error = await MediaService.save_voice(
        file_data, str(other_user_id if not match_id else match_id), str(message_id), duration
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    # Create message (voice messages have no text content)
    new_message = Message(
        id=message_id,
        match_id=match_id,
        sender_id=current_user.id,
        receiver_id=other_user_id,
        message_type="voice",
        media_url=media_url,
        media_duration=duration,
        is_sent=True,
        is_accepted=True,
    )
    session.add(new_message)
    await session.commit()

    # Offload WebSocket notification to background
    message_data = {
        "type": "new_message",
        "data": {
            "id": str(new_message.id),
            "message_type": "voice",
            "media_url": media_url,
            "duration": duration,
            "sender_id": str(current_user.id),
            "sent_at": new_message.sent_at.isoformat(),
        }
    }
    background_tasks.add_task(
        _background_websocket_send,
        match_id_str=str(match_id) if match_id else str(other_user_id),
        sender_id_str=str(current_user.id),
        message_data=message_data,
        other_user_id_str=str(other_user_id),
        redis_client=redis_client,
    )

    return SendMessageResponse(
        id=new_message.id,
        sent_at=new_message.sent_at,
        requires_acceptance=False,
        chat_accepted=True
    )


@router.post("/{identifier}/accept", response_model=AcceptChatResponse)
@limiter.limit("20/minute")
async def accept_chat(
    request: Request,
    identifier: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AcceptChatResponse:
    """Accept an unmatched chat (allows unlimited messages)"""
    match, other_user_id, match_id = await get_match_or_chat(session, identifier, current_user.id)

    if not match and not other_user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    # For unmatched chat, the other user is the identifier
    await accept_unmatched_chat(session, match_id, identifier, current_user.id)

    return AcceptChatResponse(
        message="Chat accepted. You can now send unlimited messages.",
        is_accepted=True
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
    new_message, error = await forward_message(session, message_id, body.target_match_id, current_user.id)
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
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
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
        is_read=message.is_read
    )