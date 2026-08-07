from uuid import UUID
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class ReportMessageRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    description: Optional[str] = Field(None, max_length=2000)


class ReportResponse(BaseModel):
    id: UUID
    reported_user_id: Optional[UUID] = None
    message_id: Optional[UUID] = None
    reason: str
    status: str
    created_at: datetime
    is_message_report: Optional[bool] = False
    description: Optional[str] = None

    class Config:
        from_attributes = True