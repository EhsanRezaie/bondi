from uuid import UUID
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


# User Management Schemas
class AdminUserPhotoResponse(BaseModel):
    id: UUID
    url: str
    is_main: bool = False
    status: str
    reject_reason: Optional[str] = None
    face_verified: bool = False
    order: int = 0
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserResponse(BaseModel):
    # Account
    id: UUID
    email: str
    phone: Optional[str] = None
    phone_verified: bool
    google_id: Optional[str] = None
    registration_status: Optional[str] = None
    token_version: Optional[int] = None
    referral_code: Optional[str] = None
    referred_by: Optional[UUID] = None
    is_active: bool
    created_at: datetime
    last_seen_at: Optional[datetime] = None

    # Profile / identity
    name: str
    birth_date: Optional[date] = None
    age: int
    gender: Optional[str] = None
    sexual_orientation: Optional[str] = None
    bio: Optional[str] = None

    # Appearance
    height: Optional[int] = None
    weight: Optional[int] = None
    body_type: Optional[str] = None

    # Lifestyle
    relationship_status: Optional[str] = None
    living_situation: Optional[str] = None
    children_status: Optional[str] = None
    smoking: Optional[str] = None
    drinking: Optional[str] = None

    # Background
    languages: Optional[List[str]] = None
    education: Optional[str] = None
    workplace: Optional[str] = None
    religion: Optional[str] = None
    ethnicity: Optional[str] = None
    political_orientation: Optional[str] = None

    # Location
    lat: Optional[float] = None
    lng: Optional[float] = None
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    location_manual: Optional[bool] = None

    # Verification / premium
    is_verified: Optional[bool] = None
    verified_at: Optional[datetime] = None
    is_premium: bool
    premium_until: Optional[datetime] = None

    # Settings
    hide_last_seen: bool = False
    hide_online_status: bool = False

    # Relations
    interests: Optional[List[str]] = None
    photos: Optional[List[AdminUserPhotoResponse]] = None

    # Stats
    total_likes_sent: Optional[int] = None
    total_matches: Optional[int] = None
    total_messages: Optional[int] = None
    report_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserUpdate(BaseModel):
    is_active: Optional[bool] = None
    premium_until: Optional[datetime] = None


class AdminPremiumGrant(BaseModel):
    days: int = Field(..., ge=1, le=365)


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int
    next_offset: Optional[int] = None


# Report Management Schemas
class AdminReportResponse(BaseModel):
    id: UUID
    reporter_id: UUID
    reporter_name: str
    reported_id: UUID
    reported_name: str
    reason: str
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminReportUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|reviewed|action_taken)$")
    admin_note: Optional[str] = Field(None, max_length=500)


# Ticket Management Schemas
class AdminTicketResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    user_email: str
    subject: str
    message: str
    status: str
    admin_response: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminTicketUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(open|in_progress|closed)$")
    admin_response: Optional[str] = Field(None, max_length=2000)


# Photo Management Schemas
class AdminPhotoDetailResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    user_email: str
    url: str
    is_main: bool
    status: str
    reject_reason: Optional[str] = None
    face_verified: bool
    order: int
    created_at: datetime

    class Config:
        from_attributes = True


# Message & Announcement Schemas - ADD THESE
class AdminMessageRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=2000)
    # Optional explicit recipient for the "test announcement" flow. When omitted,
    # the test endpoint falls back to the legacy admin@test.com user if it exists.
    target_user_id: Optional[UUID] = None


class AdminMessageResponse(BaseModel):
    success: bool
    message: str
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None


class AdminAnnouncementRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=2000)
    to_premium_only: bool = False


class AdminAnnouncementResponse(BaseModel):
    success: bool
    message: str
    recipient_count: int


# Photo moderation pending list
class AdminPendingPhotoResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    user_email: str
    url: str
    is_main: bool
    status: str
    face_verified: bool = False
    created_at: Optional[str] = None


class AdminPhotoActionResponse(BaseModel):
    message: str
    photo_id: str


class AdminPhotoRejectResponse(BaseModel):
    message: str
    photo_id: str
    reason: str


class AdminPhotoVerifyResponse(BaseModel):
    message: str
    photo_id: str
    face_verified: bool


class AdminPhotoStatsResponse(BaseModel):
    pending: int
    approved: int
    rejected: int
    total: int


class AdminUserPhotoResponse(BaseModel):
    id: UUID
    url: str
    is_main: bool
    status: str
    reject_reason: Optional[str] = None
    face_verified: bool = False
    order: int
    created_at: Optional[str] = None


# Message moderation
class AdminMessageDecryptResponse(BaseModel):
    message_id: str
    sender_id: str
    receiver_id: str
    chat_id: str
    content: str
    sent_at: Optional[str] = None


class AdminMessageDeleteResponse(BaseModel):
    message: str
    message_id: str
    reason: str


class AdminReportedMessageResponse(BaseModel):
    report_id: str
    message_id: str
    content: str
    sender_id: str
    receiver_id: str
    sent_at: Optional[str] = None
    report_reason: str
    report_description: Optional[str] = None


# User activity
class UserActivityEntry(BaseModel):
    date: str
    swipes: int
    matches: int
    messages: int


# Audit log
class AdminLogEntry(BaseModel):
    id: UUID
    admin_id: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[UUID] = None
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminLogListResponse(BaseModel):
    logs: list[AdminLogEntry]
    total: int
    page: int
    page_size: int