from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=10, max_length=2000)


class TicketMessageCreate(BaseModel):
    content: str = Field(..., min_length=10, max_length=2000)


class TicketMessageResponse(BaseModel):
    id: UUID
    sender_type: str
    admin_name: str | None = None
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
    admin_response: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    messages: list[TicketMessageResponse] = []

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]
    total: int
    next_offset: int | None = None


class AdminTicketUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(open|in_progress|closed)$")
    admin_response: str | None = Field(None, max_length=2000)
