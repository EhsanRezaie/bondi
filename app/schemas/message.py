from uuid import UUID
from datetime import datetime
from typing import Optional, List, Literal, Union
from pydantic import BaseModel, Field, model_validator


class ReplyToResponse(BaseModel):
    """Response for replied-to message"""
    id: UUID
    content: str
    sender_name: str
    message_type: str


class MessageResponse(BaseModel):
    """Response for a single message"""
    id: UUID
    client_id: Optional[UUID] = None
    chat_id: UUID
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
    is_edited: bool = False
    sent_at: datetime
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    edited_at: Optional[datetime] = None

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
    client_id: Optional[UUID] = None


class PhotoMessageRequest(BaseModel):
    """Request for sending photo message"""
    caption: Optional[str] = Field(None, max_length=500)


class VoiceMessageRequest(BaseModel):
    """Request for sending voice message"""
    duration: int = Field(..., ge=1, le=120)  # 1-120 seconds


class DeleteMessageRequest(BaseModel):
    """Request for deleting message"""
    delete_for: str = Field("me", pattern="^(me|everyone)$")  # 'me' or 'everyone'


class EditMessageRequest(BaseModel):
    """Request for editing a message"""
    content: str = Field(..., min_length=1, max_length=5000)


class ForwardMessageRequest(BaseModel):
    """Request for forwarding message"""
    target_chat_id: UUID


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


# ---------------------------------------------------------------------------
# WebSocket inbound message models (P3-1)
# ---------------------------------------------------------------------------
WS_MAX_READ_IDS = 200


class WsPingInbound(BaseModel):
    type: Literal["ping"]


class WsSubscribeInbound(BaseModel):
    type: Literal["subscribe"]
    chat_id: UUID


class WsUnsubscribeInbound(BaseModel):
    type: Literal["unsubscribe"]
    chat_id: UUID


class WsTypingInbound(BaseModel):
    type: Literal["typing", "typing_stopped"]


class WsReadInbound(BaseModel):
    type: Literal["read"]
    message_ids: List[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cap_message_ids(self):
        if len(self.message_ids) > WS_MAX_READ_IDS:
            raise ValueError("too_many_message_ids")
        return self


class WsInbound(BaseModel):
    """Union of all accepted inbound WS messages."""
    type: str
    chat_id: Optional[UUID] = None
    message_ids: Optional[List[UUID]] = None

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.type == "subscribe":
            if self.chat_id is None:
                raise ValueError("missing_chat_id")
        elif self.type == "unsubscribe":
            if self.chat_id is None:
                raise ValueError("missing_chat_id")
        elif self.type in ("ping", "typing", "typing_stopped"):
            pass
        elif self.type == "read":
            ids = self.message_ids or []
            if len(ids) > WS_MAX_READ_IDS:
                raise ValueError("too_many_message_ids")
        else:
            raise ValueError("unknown_type")
        return self