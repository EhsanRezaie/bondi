from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class ReportMessageRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    description: str | None = Field(None, max_length=2000)


class ReportResponse(BaseModel):
    id: UUID
    reported_user_id: UUID | None = None
    message_id: UUID | None = None
    reason: str
    status: str
    created_at: datetime
    is_message_report: bool | None = False
    description: str | None = None

    class Config:
        from_attributes = True
