from uuid import UUID
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=10, max_length=2000)


class TicketMessageCreate(BaseModel):
    content: str = Field(..., min_length=10, max_length=2000)


class TicketMessageResponse(BaseModel):
    id: UUID
    sender_type: str
    admin_name: Optional[str] = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class TicketResponse(BaseModel):
    id: UUID
    user_id: UUID
    subject: str
    message: str
    status: str
    admin_response: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    messages: list[TicketMessageResponse] = []

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]
    total: int
    next_offset: Optional[int] = None


class AdminTicketUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(open|in_progress|closed)$")
    admin_response: Optional[str] = Field(None, max_length=2000)
