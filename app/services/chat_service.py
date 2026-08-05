# app/services/chat_service.py
import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.services.reward_service import RewardService
from app.services.notification_service import NotificationService
from app.models.user import User
from app.models.match import Match
from app.models.message import Message
from app.models.daily_limit import DailyLimit
from app.core.logging import get_logger
from app.core.encryption import encrypt_message, decrypt_message, decrypt_message_async

logger = get_logger("chat_service")


async def get_or_create_daily_limit(
    session: AsyncSession,
    user_id: UUID,
    target_date: date
) -> DailyLimit:
    """Get or create daily limit record for a user"""
    stmt = insert(DailyLimit).values(
        user_id=user_id,
        date=target_date,
        likes_used=0,
        chats_used=0,
        ad_likes_bonus=0,
        ad_chats_bonus=0,
    ).on_conflict_do_nothing(
        constraint="uq_daily_limits_user_date"
    ).returning(DailyLimit)

    result = await session.execute(stmt)
    daily_limit = result.scalar_one()
    await session.flush()

    return daily_limit


async def can_start_new_chat(
    session: AsyncSession,
    user_id: UUID,
    target_user_id: UUID,
    is_premium: bool
) -> Tuple[bool, Optional[str], Optional[DailyLimit]]:
    """
    Check if user can start a new chat with target user.
    Returns: (can_start, reason, daily_limit)
    """
    # Premium users have no limits
    if is_premium:
        return True, None, None

    # Check if they already have a chat (not a new chat)
    existing = await session.execute(
        select(Message).where(
            or_(
                and_(Message.sender_id == user_id, Message.receiver_id == target_user_id),
                and_(Message.sender_id == target_user_id, Message.receiver_id == user_id)
            )
        ).limit(1)
    )

    if existing.scalar_one_or_none():
        return True, None, None

    # This is a new chat - check daily limit using RewardService
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user:
        reward_service = RewardService(session)
        remaining = await reward_service.get_remaining_chats(user)
        
        if remaining <= 0:
            return False, f"Daily new chat limit reached ({settings.FREE_USER_DAILY_CHATS} per day). Watch an ad or upgrade to premium.", None

    return True, None, None


async def check_unmatched_message_limit(
    session: AsyncSession,
    sender_id: UUID,
    receiver_id: UUID,
    match_id: Optional[UUID]
) -> Tuple[bool, Optional[str], bool]:
    """
    Check if user can send message in unmatched chat.
    Returns: (can_send, reason, is_accepted)
    """
    if match_id:
        return True, None, True

    # Count messages sent in this unmatched chat
    result = await session.execute(
        select(func.count(Message.id)).where(
            Message.sender_id == sender_id,
            Message.receiver_id == receiver_id,
            Message.match_id.is_(None),
            Message.is_deleted_for_all == False,
            Message.is_deleted_for_sender == False
        )
    )
    message_count = result.scalar() or 0

    # Check if receiver has accepted
    result = await session.execute(
        select(Message.is_accepted).where(
            Message.sender_id == receiver_id,
            Message.receiver_id == sender_id,
            Message.match_id.is_(None),
            Message.is_accepted == True
        ).limit(1)
    )
    is_accepted_by_receiver = result.scalar_one_or_none() or False

    # Check if sender has accepted
    result = await session.execute(
        select(Message.is_accepted).where(
            Message.sender_id == sender_id,
            Message.receiver_id == receiver_id,
            Message.match_id.is_(None),
            Message.is_accepted == True
        ).limit(1)
    )
    is_accepted_by_sender = result.scalar_one_or_none() or False

    is_accepted = is_accepted_by_receiver or is_accepted_by_sender

    if is_accepted:
        return True, None, True

    if message_count >= 2:
        return False, "Recipient must accept the conversation before sending more messages. They have received 2 messages already.", False

    return True, None, False


async def accept_unmatched_chat(
    session: AsyncSession,
    match_id: Optional[UUID],
    other_user_id: UUID,
    current_user_id: UUID
) -> bool:
    """Accept an unmatched chat - updates ALL messages between the two users"""
    
    if match_id:
        await session.execute(
            update(Message)
            .where(Message.match_id == match_id)
            .values(is_accepted=True)
        )
    else:
        await session.execute(
            update(Message)
            .where(
                or_(
                    and_(
                        Message.sender_id == current_user_id,
                        Message.receiver_id == other_user_id
                    ),
                    and_(
                        Message.sender_id == other_user_id,
                        Message.receiver_id == current_user_id
                    )
                ),
                Message.match_id.is_(None)
            )
            .values(is_accepted=True)
        )

    await session.commit()
    return True


async def increment_new_chat_count(
    session: AsyncSession,
    user_id: UUID
) -> None:
    """Increment the new chats counter for a user using RewardService"""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user:
        reward_service = RewardService(session)
        await reward_service.consume_chat(user)


async def create_encrypted_message(
    session: AsyncSession,
    match_id: Optional[UUID],
    sender_id: UUID,
    receiver_id: UUID,
    content: str,
    message_type: str = "text",
    reply_to_id: Optional[UUID] = None,
    is_accepted: bool = True,
    media_url: Optional[str] = None,
    media_duration: Optional[int] = None,
) -> Message:
    """
    Create a new message with encrypted content.
    """
    # Create message object
    new_message = Message(
        match_id=match_id,
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type=message_type,
        reply_to_id=reply_to_id,
        is_sent=True,
        is_accepted=is_accepted,
        media_url=media_url,
        media_duration=media_duration,
    )
    
    # Set content (will be encrypted via property setter if match_id exists)
    if content and match_id:
        new_message.content = content
    else:
        new_message._content = content  # Store as-is if no match_id
    
    session.add(new_message)
    await session.flush()
    return new_message


async def send_message_with_encryption(
    session: AsyncSession,
    sender_id: UUID,
    receiver_id: UUID,
    match_id: Optional[UUID],
    sender_name: str,
    content: str,
    message_type: str = "text",
    reply_to_id: Optional[UUID] = None,
) -> Message:
    """
    Send a message with encryption and create notification.
    """
    # Create message with encryption
    new_message = await create_encrypted_message(
        session=session,
        match_id=match_id,
        sender_id=sender_id,
        receiver_id=receiver_id,
        content=content,
        message_type=message_type,
        reply_to_id=reply_to_id,
        is_accepted=match_id is not None,
    )
    
    # Send notification
    notification_service = NotificationService(session)
    await notification_service.notify_message(
        receiver_id=receiver_id,
        sender_name=sender_name,
        match_id=match_id or receiver_id
    )
    
    return new_message


async def get_decrypted_message_for_client(
    session: AsyncSession,
    message: Message,
    current_user_id: UUID
) -> Dict[str, Any]:
    """
    Get message data with decrypted content for client delivery.
    Decrypt runs in a threadpool to avoid blocking the event loop.
    """
    # Decrypt content off the event loop
    if message.match_id and message._content:
        decrypted_content = await decrypt_message_async(message._content, str(message.match_id))
    else:
        decrypted_content = message._content

    return {
        "id": str(message.id),
        "match_id": str(message.match_id) if message.match_id else None,
        "sender_id": str(message.sender_id),
        "receiver_id": str(message.receiver_id),
        "message_type": message.message_type,
        "content": decrypted_content,
        "media_url": message.media_url,
        "media_duration": message.media_duration,
        "reply_to_id": str(message.reply_to_id) if message.reply_to_id else None,
        "is_sent": message.is_sent,
        "is_delivered": message.is_delivered,
        "is_read": message.is_read,
        "is_accepted": message.is_accepted,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
    }


async def get_message_for_admin(
    session: AsyncSession,
    message_id: UUID
) -> Tuple[Optional[Message], Optional[str]]:
    """
    Get a message with decrypted content for admin review.
    Decrypt runs in a threadpool to avoid blocking the event loop.
    Returns: (message, decrypted_content)
    """
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        return None, None

    # Decrypt off the event loop
    if message.match_id and message._content:
        decrypted_content = await decrypt_message_async(message._content, str(message.match_id))
    else:
        decrypted_content = message._content

    return message, decrypted_content


async def mark_messages_as_delivered(
    session: AsyncSession,
    message_ids: list[UUID],
    user_id: UUID
) -> None:
    """Mark messages as delivered"""
    await session.execute(
        update(Message)
        .where(
            Message.id.in_(message_ids),
            Message.receiver_id == user_id,
            Message.is_delivered == False
        )
        .values(is_delivered=True, delivered_at=datetime.utcnow())
    )
    await session.commit()


async def mark_messages_as_read(
    session: AsyncSession,
    message_ids: list[UUID],
    user_id: UUID
) -> None:
    """Mark messages as read"""
    await session.execute(
        update(Message)
        .where(
            Message.id.in_(message_ids),
            Message.receiver_id == user_id,
            Message.is_read == False
        )
        .values(is_read=True, read_at=datetime.utcnow())
    )
    await session.commit()


async def delete_message(
    session: AsyncSession,
    message_id: UUID,
    user_id: UUID,
    delete_for: str
) -> Tuple[bool, Optional[str]]:
    """Delete a message. Returns: (success, error_message)"""
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        return False, "Message not found"

    if message.sender_id != user_id and message.receiver_id != user_id:
        return False, "Not authorized to delete this message"

    if delete_for == "everyone":
        if message.sender_id != user_id:
            return False, "Only the sender can delete for everyone"

        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        if message.sent_at < one_hour_ago:
            return False, "Cannot delete for everyone after 1 hour. Use delete for me instead."

        message.is_deleted_for_all = True
        message.deleted_at = datetime.now(timezone.utc)
        message._content = "[Message deleted]"  # Store as-is (not encrypted)
    else:
        if message.sender_id == user_id:
            message.is_deleted_for_sender = True
        else:
            message.is_deleted_for_receiver = True

    await session.commit()
    return True, None


async def forward_message(
    session: AsyncSession,
    message_id: UUID,
    target_match_id: UUID,
    user_id: UUID
) -> Tuple[Optional[Message], Optional[str]]:
    """Forward a message to another chat. Returns: (new_message, error_message)"""
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
    original = result.scalar_one_or_none()

    if not original:
        return None, "Message not found"

    if original.sender_id != user_id and original.receiver_id != user_id:
        return None, "Not authorized to forward this message"

    result = await session.execute(
        select(Match).where(Match.id == target_match_id, Match.is_active == True)
    )
    target_match = result.scalar_one_or_none()

    if not target_match:
        return None, "Target match not found"

    if target_match.user1_id != user_id and target_match.user2_id != user_id:
        return None, "Not part of target match"

    receiver_id = target_match.user2_id if target_match.user1_id == user_id else target_match.user1_id

    # Get decrypted content for forwarding (off the event loop)
    if original.match_id and original._content:
        original_content = await decrypt_message_async(original._content, str(original.match_id))
    else:
        original_content = original._content

    forwarded_content = f"📨 Forwarded: {original_content}" if original_content else "📨 Forwarded message"

    # Create new message with encryption for new chat
    new_message = await create_encrypted_message(
        session=session,
        match_id=target_match_id,
        sender_id=user_id,
        receiver_id=receiver_id,
        content=forwarded_content,
        message_type=original.message_type,
        is_accepted=True,
        media_url=original.media_url,
        media_duration=original.media_duration,
    )

    return new_message, None