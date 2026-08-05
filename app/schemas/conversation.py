# app/schemas/conversation.py
from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ConversationUserResponse(BaseModel):
    """Other user info within a conversation"""
    id: UUID
    name: str
    age: int
    main_photo_url: Optional[str] = None
    is_online: bool = False
    last_seen_at: Optional[datetime] = None


class ConversationLastMessage(BaseModel):
    """Last message preview in a conversation"""
    content: Optional[str] = None
    message_type: str = "text"
    is_sent: bool = True
    is_read: bool = False
    sent_at: datetime


class ConversationResponse(BaseModel):
    """A single conversation (match or unmatched chat)"""
    id: UUID
    kind: str  # "match" | "unmatched"
    user: ConversationUserResponse
    last_message: Optional[ConversationLastMessage] = None
    is_accepted: bool = False
    unread_count: int = 0
    updated_at: Optional[datetime] = None


class ConversationListResponse(BaseModel):
    """Response for conversations list"""
    conversations: List[ConversationResponse]
    total: int
    next_offset: Optional[int] = None