from uuid import UUID
from datetime import datetime
from typing import Optional, List, Literal, Dict
from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: UUID
    type: str
    title: str
    body: Optional[str] = None
    data: Optional[dict] = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MarkReadRequest(BaseModel):
    notification_ids: List[UUID]


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    next_offset: Optional[int] = None


class NotificationCountsResponse(BaseModel):
    total: int
    by_type: Dict[str, int]


class DeviceTokenRequest(BaseModel):
    token: str
    platform: Literal["android", "ios"]


class DeviceTokenResponse(BaseModel):
    id: UUID
    token: str
    platform: str

    class Config:
        from_attributes = True