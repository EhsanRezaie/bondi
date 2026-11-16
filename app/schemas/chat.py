# app/schemas/chat.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ChatUserResponse(BaseModel):
    """Other user info within a chat"""

    id: UUID
    name: str
    age: int
    main_photo_url: str | None = None
    main_photo_thumb_url: str | None = None
    is_online: bool = False
    last_seen_at: datetime | None = None


class ChatLastMessage(BaseModel):
    """Last message preview in a chat"""

    content: str | None = None
    message_type: str = "text"
    is_sent: bool = True
    is_read: bool = False
    sent_at: datetime


class ChatItemResponse(BaseModel):
    """A single chat in the chat list"""

    id: UUID
    status: str  # 'pending' | 'accepted'
    initiator_id: UUID
    user: ChatUserResponse
    last_message: ChatLastMessage | None = None
    unread_count: int = 0
    updated_at: datetime | None = None
    is_blocked: bool = False
    is_ended: bool = False


class ChatListResponse(BaseModel):
    """Response for the chat list"""

    chats: list[ChatItemResponse]
    total: int
    next_offset: int | None = None
    next_cursor: str | None = None


class ChatDetailResponse(BaseModel):
    """Detailed chat response (other user + status)"""

    id: UUID
    status: str
    initiator_id: UUID
    recipient_id: UUID
    user: ChatUserResponse
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_blocked: bool = False
    is_ended: bool = False


class ChatCreateRequest(BaseModel):
    """Request to start (or enter) a chat"""

    user_id: UUID
    content: str = Field(..., min_length=1, max_length=5000)


class ChatCreateResponse(BaseModel):
    """Response after creating/entering a chat"""

    chat_id: UUID
    is_new: bool
    status: str
    message: object | None = None
    chats_remaining_today: int | None = None
    created_at: datetime | None = None


class ChatAcceptResponse(BaseModel):
    """Response for accepting a chat"""

    chat_id: UUID
    status: str
    message: str
