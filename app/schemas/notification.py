from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: UUID
    type: str
    title: str
    body: str | None = None
    data: dict | None = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MarkReadRequest(BaseModel):
    notification_ids: list[UUID]


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    total: int
    next_offset: int | None = None
    next_cursor: str | None = None


class NotificationCountsResponse(BaseModel):
    total: int
    by_type: dict[str, int]


class DeviceTokenRequest(BaseModel):
    token: str
    platform: Literal["android", "ios"]


class DeviceTokenResponse(BaseModel):
    id: UUID
    token: str
    platform: str

    class Config:
        from_attributes = True
