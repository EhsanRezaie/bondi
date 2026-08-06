# app/schemas/chat.py
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ChatUserResponse(BaseModel):
    """Other user info within a chat"""
    id: UUID
    name: str
    age: int
    main_photo_url: Optional[str] = None
    is_online: bool = False
    last_seen_at: Optional[datetime] = None


class ChatLastMessage(BaseModel):
    """Last message preview in a chat"""
    content: Optional[str] = None
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
    last_message: Optional[ChatLastMessage] = None
    unread_count: int = 0
    updated_at: Optional[datetime] = None


class ChatListResponse(BaseModel):
    """Response for the chat list"""
    chats: List[ChatItemResponse]
    total: int
    next_offset: Optional[int] = None


class ChatDetailResponse(BaseModel):
    """Detailed chat response (other user + status)"""
    id: UUID
    status: str
    initiator_id: UUID
    recipient_id: UUID
    user: ChatUserResponse
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChatCreateRequest(BaseModel):
    """Request to start (or enter) a chat"""
    user_id: UUID
    content: str = Field(..., min_length=1, max_length=5000)


class ChatCreateResponse(BaseModel):
    """Response after creating/entering a chat"""
    chat_id: UUID
    is_new: bool
    status: str
    message: Optional[object] = None
    chats_remaining_today: Optional[int] = None
    created_at: Optional[datetime] = None


class ChatAcceptResponse(BaseModel):
    """Response for accepting a chat"""
    chat_id: UUID
    status: str
    message: str
