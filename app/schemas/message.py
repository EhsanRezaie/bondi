from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ReplyToResponse(BaseModel):
    """Response for replied-to message"""
    id: UUID
    content: str
    sender_name: str
    message_type: str


class MessageResponse(BaseModel):
    """Response for a single message"""
    id: UUID
    match_id: Optional[UUID] = None
    sender_id: UUID
    receiver_id: UUID
    message_type: str  # text, photo, voice
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_duration: Optional[int] = None
    reply_to: Optional[ReplyToResponse] = None
    is_sent: bool
    is_delivered: bool
    is_read: bool
    is_accepted: bool
    sent_at: datetime
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MessageListResponse(BaseModel):
    """Response for chat history"""
    messages: List[MessageResponse]
    total: int
    next_offset: Optional[int] = None


class TextMessageRequest(BaseModel):
    """Request for sending text message"""
    content: str = Field(..., min_length=1, max_length=5000)
    reply_to_id: Optional[UUID] = None


class PhotoMessageRequest(BaseModel):
    """Request for sending photo message"""
    caption: Optional[str] = Field(None, max_length=500)


class VoiceMessageRequest(BaseModel):
    """Request for sending voice message"""
    duration: int = Field(..., ge=1, le=120)  # 1-120 seconds


class DeleteMessageRequest(BaseModel):
    """Request for deleting message"""
    delete_for: str = Field("me", pattern="^(me|everyone)$")  # 'me' or 'everyone'


class ForwardMessageRequest(BaseModel):
    """Request for forwarding message"""
    target_match_id: UUID


class MarkReadRequest(BaseModel):
    """Request for marking messages as read"""
    message_ids: List[UUID]


class MessageStatusResponse(BaseModel):
    """Response for message status"""
    id: UUID
    sent_at: datetime
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    is_delivered: bool
    is_read: bool


class AcceptChatResponse(BaseModel):
    """Response for accepting chat"""
    message: str
    is_accepted: bool


class SendMessageResponse(BaseModel):
    """Response after sending message"""
    id: UUID
    sent_at: datetime
    requires_acceptance: bool = False
    chat_accepted: bool = True
    chats_remaining_today: Optional[int] = None
    message: Optional[MessageResponse] = None


class MessageActionResponse(BaseModel):
    """Response for simple message actions (delivered, read, delete)."""
    message: str


class ForwardMessageResponse(BaseModel):
    """Response for forwarding a message."""
    message: str
    new_message_id: str