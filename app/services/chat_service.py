# app/services/chat_service.py
import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, update
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.services.reward_service import RewardService
from app.services.notification_service import NotificationService
from app.models.user import User
from app.models.chat import Chat
from app.models.message import Message
from app.models.swipe import Swipe
from app.models.block import Block
from app.models.daily_limit import DailyLimit
from app.core.logging import get_logger
from app.core.encryption import encrypt_message, decrypt_message, decrypt_message_async

logger = get_logger("chat_service")

# Max starter messages the initiator can send while a chat is pending.
PENDING_INITIATOR_MESSAGE_LIMIT = 2


async def get_or_create_daily_limit(
    session: AsyncSession,
    user_id: UUID,
    target_date: date
) -> DailyLimit:
    """Get or create daily limit record for a user"""
    stmt = select(DailyLimit).where(
        DailyLimit.user_id == user_id,
        DailyLimit.date == target_date,
    )
    daily_limit = (await session.execute(stmt)).scalar_one_or_none()
    if daily_limit:
        return daily_limit
    daily_limit = DailyLimit(
        user_id=user_id,
        date=target_date,
        likes_used=0,
        chats_used=0,
        ad_likes_bonus=0,
        ad_chats_bonus=0,
    )
    session.add(daily_limit)
    await session.flush()
    return daily_limit


async def find_chat_for_pair(
    session: AsyncSession,
    user1_id: UUID,
    user2_id: UUID,
    only_active: bool = True,
) -> Optional[Chat]:
    """Find the active chat between a pair regardless of stored order."""
    stmt = select(Chat).where(
        or_(
            (Chat.initiator_id == user1_id) & (Chat.recipient_id == user2_id),
            (Chat.initiator_id == user2_id) & (Chat.recipient_id == user1_id),
        )
    )
    if only_active:
        stmt = stmt.where(Chat.is_active == True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_last_messages_for_chats(
    session: AsyncSession,
    chat_ids: list[UUID],
) -> Dict[UUID, Message]:
    """Return the newest message per chat for the given chat ids."""
    if not chat_ids:
        return {}
    result = await session.execute(
        select(Message)
        .where(Message.chat_id.in_(chat_ids), Message.is_deleted_for_all == False)
        .order_by(Message.chat_id, Message.sent_at.desc())
        .distinct(Message.chat_id)
    )
    return {m.chat_id: m for m in result.scalars().all()}


async def get_user_chat(
    session: AsyncSession,
    chat_id: UUID,
    user_id: UUID,
) -> Optional[Chat]:
    """Get an active chat the user is a member of (or None)."""
    return (
        await session.execute(
            select(Chat).where(
                Chat.id == chat_id,
                Chat.is_active == True,
                or_(
                    Chat.initiator_id == user_id,
                    Chat.recipient_id == user_id,
                ),
            )
        )
    ).scalar_one_or_none()


async def chat_is_ended(
    session: AsyncSession,
    chat: Chat,
    user_id: UUID,
) -> bool:
    """True when the conversation is over for this user (blocked either
    direction, or the chat was ended/deleted by a participant)."""
    me_initiator = chat.initiator_id == user_id
    peer_deleted = (
        chat.deleted_for_recipient if me_initiator else chat.deleted_for_initiator
    )
    if chat.is_ended or peer_deleted:
        return True
    blocked = await session.scalar(
        select(Block.id).where(
            or_(
                (Block.blocker_id == chat.initiator_id)
                & (Block.blocked_id == chat.recipient_id),
                (Block.blocker_id == chat.recipient_id)
                & (Block.blocked_id == chat.initiator_id),
            )
        ).limit(1)
    )
    return bool(blocked)


async def create_chat_for_pair(
    session: AsyncSession,
    initiator_id: UUID,
    recipient_id: UUID,
    status: str,
) -> Chat:
    """Create a chat with semantic initiator/recipient (initiator = who started it)."""
    chat = Chat(
        initiator_id=initiator_id,
        recipient_id=recipient_id,
        status=status,
        is_active=True,
    )
    session.add(chat)
    await session.flush()
    return chat


async def have_mutual_like(
    session: AsyncSession,
    user1_id: UUID,
    user2_id: UUID,
) -> bool:
    """True if both users have 'like' swipes toward each other."""
    result = await session.execute(
        select(func.count(Swipe.id)).where(
            or_(
                (Swipe.from_user == user1_id) & (Swipe.to_user == user2_id)
                & (Swipe.direction == "like"),
                (Swipe.from_user == user2_id) & (Swipe.to_user == user1_id)
                & (Swipe.direction == "like"),
            )
        )
    )
    return (result.scalar() or 0) >= 2


async def can_start_new_chat(
    session: AsyncSession,
    user_id: UUID,
    is_premium: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Check if the user may start a NEW chat (consumes a daily chat slot).
    Returns: (can_start, reason)
    """
    if is_premium:
        return True, None

    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        return False, "User not found"

    reward_service = RewardService(session)
    remaining = await reward_service.get_remaining_chats(user)
    if remaining <= 0:
        return False, f"Daily new chat limit reached ({settings.FREE_USER_DAILY_CHATS} per day). Watch an ad or upgrade to premium."

    return True, None


async def consume_new_chat(
    session: AsyncSession,
    user_id: UUID,
    is_premium: bool,
) -> bool:
    """Consume one daily chat slot for a new chat. No-op for premium."""
    if is_premium:
        return True
    reward_service = RewardService(session)
    return await reward_service.consume_chat(user_id)


async def can_send_message(
    session: AsyncSession,
    chat: Chat,
    sender_id: UUID,
) -> Tuple[bool, Optional[str]]:
    """
    Check whether a user may send a message in the chat.
    Pending chats: the initiator may send at most 2 starter messages;
    the recipient is unrestricted. Returns (can_send, reason).
    """
    if chat.status == "accepted":
        return True, None

    if sender_id == chat.recipient_id:
        return True, None

    count = await session.scalar(
        select(func.count(Message.id)).where(
            Message.chat_id == chat.id,
            Message.sender_id == chat.initiator_id,
            Message.is_deleted_for_all == False,
            Message.is_deleted_for_sender == False,
        )
    )
    if (count or 0) >= PENDING_INITIATOR_MESSAGE_LIMIT:
        return False, "Recipient must accept the conversation before sending more messages. They have received 2 messages already."

    return True, None


async def accept_chat(
    session: AsyncSession,
    chat: Chat,
) -> bool:
    """Mark a chat as accepted."""
    chat.status = "accepted"
    await session.commit()
    return True


async def create_encrypted_message(
    session: AsyncSession,
    chat_id: UUID,
    sender_id: UUID,
    receiver_id: UUID,
    content: str,
    message_type: str = "text",
    reply_to_id: Optional[UUID] = None,
    media_url: Optional[str] = None,
    media_duration: Optional[int] = None,
) -> Message:
    """Create a new message with encrypted content (always encrypted)."""
    new_message = Message(
        chat_id=chat_id,
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type=message_type,
        reply_to_id=reply_to_id,
        is_sent=True,
        media_url=media_url,
        media_duration=media_duration,
    )
    if content:
        new_message.content = content  # encrypted via property setter
    session.add(new_message)
    await session.flush()
    return new_message


async def get_decrypted_message_for_client(
    session: AsyncSession,
    message: Message,
    current_user_id: UUID,
) -> Dict[str, Any]:
    """
    Get message data with decrypted content for client delivery.
    Decrypt runs in a threadpool to avoid blocking the event loop.
    """
    decrypted_content = message._content
    if message.chat_id and message._content:
        decrypted_content = await decrypt_message_async(message._content, str(message.chat_id))

    return {
        "id": str(message.id),
        "chat_id": str(message.chat_id),
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
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
    }


async def get_message_for_admin(
    session: AsyncSession,
    message_id: UUID,
) -> Tuple[Optional[Message], Optional[str]]:
    """
    Get a message with decrypted content for admin review.
    Decrypt runs in a threadpool to avoid blocking the event loop.
    Returns: (message, decrypted_content)
    """
    result = await session.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()

    if not message:
        return None, None

    if message.chat_id and message._content:
        decrypted_content = await decrypt_message_async(message._content, str(message.chat_id))
    else:
        decrypted_content = message._content

    return message, decrypted_content


async def mark_messages_as_delivered(
    session: AsyncSession,
    message_ids: list[UUID],
    user_id: UUID,
) -> None:
    """Mark messages as delivered"""
    await session.execute(
        update(Message)
        .where(
            Message.id.in_(message_ids),
            Message.receiver_id == user_id,
            Message.is_delivered == False,
        )
        .values(is_delivered=True, delivered_at=datetime.utcnow())
    )
    await session.commit()


async def mark_messages_as_read(
    session: AsyncSession,
    message_ids: list[UUID],
    user_id: UUID,
) -> None:
    """Mark messages as read"""
    await session.execute(
        update(Message)
        .where(
            Message.id.in_(message_ids),
            Message.receiver_id == user_id,
            Message.is_read == False,
        )
        .values(is_read=True, read_at=datetime.utcnow())
    )
    await session.commit()


async def delete_message(
    session: AsyncSession,
    message_id: UUID,
    user_id: UUID,
    delete_for: str,
) -> Tuple[bool, Optional[str]]:
    """Delete a message. Returns: (success, error_message)"""
    result = await session.execute(select(Message).where(Message.id == message_id))
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


async def edit_message(
    session: AsyncSession,
    message_id: UUID,
    user_id: UUID,
    content: str,
) -> Tuple[Optional[Message], Optional[str]]:
    """Edit a message's text content. Returns: (message, error_message)."""
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        return None, "Message not found"
    if message.sender_id != user_id:
        return None, "Not authorized to edit this message"
    if message.message_type != "text":
        return None, "Only text messages can be edited"
    if message.is_deleted_for_all:
        return None, "Cannot edit a deleted message"
    if not content or not content.strip():
        return None, "Message content cannot be empty"

    message.content = content.strip()
    message.is_edited = True
    message.edited_at = datetime.now(timezone.utc)
    await session.commit()
    return message, None


async def forward_message(
    session: AsyncSession,
    message_id: UUID,
    target_chat_id: UUID,
    user_id: UUID,
) -> Tuple[Optional[Message], Optional[str]]:
    """Forward a message to another chat. Returns: (new_message, error_message)"""
    result = await session.execute(select(Message).where(Message.id == message_id))
    original = result.scalar_one_or_none()

    if not original:
        return None, "Message not found"

    if original.sender_id != user_id and original.receiver_id != user_id:
        return None, "Not authorized to forward this message"

    result = await session.execute(
        select(Chat).where(Chat.id == target_chat_id, Chat.is_active == True)
    )
    target_chat = result.scalar_one_or_none()

    if not target_chat:
        return None, "Target chat not found"

    if target_chat.initiator_id != user_id and target_chat.recipient_id != user_id:
        return None, "Not part of target chat"

    receiver_id = (
        target_chat.recipient_id if target_chat.initiator_id == user_id
        else target_chat.initiator_id
    )

    if original.chat_id and original._content:
        original_content = await decrypt_message_async(original._content, str(original.chat_id))
    else:
        original_content = original._content

    forwarded_content = f"📨 Forwarded: {original_content}" if original_content else "📨 Forwarded message"

    new_message = await create_encrypted_message(
        session=session,
        chat_id=target_chat_id,
        sender_id=user_id,
        receiver_id=receiver_id,
        content=forwarded_content,
        message_type=original.message_type,
        media_url=original.media_url,
        media_duration=original.media_duration,
    )

    return new_message, None