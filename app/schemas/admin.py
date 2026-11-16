from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ticket import TicketMessageResponse


# User Management Schemas
class AdminUserPhotoResponse(BaseModel):
    id: UUID
    url: str
    is_main: bool = False
    status: str
    reject_reason: str | None = None
    face_verified: bool = False
    order: int = 0
    created_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserResponse(BaseModel):
    # Account
    id: UUID
    phone: str | None = None
    phone_verified: bool
    email: str | None = None
    registration_status: str | None = None
    token_version: int | None = None
    referral_code: str | None = None
    referred_by: UUID | None = None
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None = None

    # Profile / identity
    name: str
    birth_date: date | None = None
    age: int
    gender: str | None = None
    sexual_orientation: str | None = None
    bio: str | None = None

    # Appearance
    height: int | None = None
    weight: int | None = None
    body_type: str | None = None

    # Lifestyle
    relationship_status: str | None = None
    living_situation: str | None = None
    children_status: str | None = None
    smoking: str | None = None
    drinking: str | None = None
    here_for: str | None = None
    pets: str | None = None
    workout_frequency: str | None = None
    zodiac_sign: str | None = None

    # Background
    languages: list[str] | None = None
    education: str | None = None
    workplace: str | None = None
    religion: str | None = None
    ethnicity: str | None = None
    political_orientation: str | None = None

    # Location
    lat: float | None = None
    lng: float | None = None
    country: str | None = None
    province: str | None = None
    city: str | None = None
    location_manual: bool | None = None

    # Verification / premium
    is_verified: bool | None = None
    verified_at: datetime | None = None
    is_premium: bool
    premium_until: datetime | None = None

    # Settings
    hide_last_seen: bool = False
    hide_online_status: bool = False

    # Relations
    interests: list[str] | None = None
    photos: list[AdminUserPhotoResponse] | None = None

    # Stats
    total_likes_sent: int | None = None
    total_matches: int | None = None
    total_messages: int | None = None
    report_count: int | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    premium_until: datetime | None = None


class AdminPremiumGrant(BaseModel):
    days: int = Field(..., ge=1, le=365)


class AdminUserListItem(BaseModel):
    """Minimal user row for the paginated admin search list."""

    id: UUID
    phone: str | None = None
    phone_verified: bool
    email: str | None = None
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None = None

    # Profile / identity
    name: str
    age: int
    gender: str | None = None
    city: str | None = None
    country: str | None = None
    province: str | None = None

    # Verification / premium
    is_verified: bool | None = None
    is_premium: bool
    premium_until: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserListResponse(BaseModel):
    users: list[AdminUserListItem]
    total: int
    next_offset: int | None = None


# Report Management Schemas
class AdminReportResponse(BaseModel):
    id: UUID
    reporter_id: UUID
    reporter_name: str
    reported_id: UUID
    reported_name: str
    reason: str
    status: str
    admin_note: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    class Config:
        from_attributes = True


class AdminReportUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|reviewed|action_taken)$")
    admin_note: str | None = Field(None, max_length=500)


# Ticket Management Schemas
class AdminTicketResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_uid: str
    user_name: str
    user_email: str | None = None
    subject: str
    message: str
    last_message: str | None = None
    status: str
    admin_response: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    messages: list["TicketMessageResponse"] | None = None

    class Config:
        from_attributes = True


class AdminTicketUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(open|in_progress|closed)$")
    admin_response: str | None = Field(None, max_length=2000)


class AdminTicketListResponse(BaseModel):
    tickets: list[AdminTicketResponse]
    total: int
    next_offset: int | None = None


# Photo Management Schemas
class AdminPhotoDetailResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    user_email: str | None = None
    url: str
    is_main: bool
    status: str
    reject_reason: str | None = None
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
    target_user_id: UUID | None = None


class AdminMessageResponse(BaseModel):
    success: bool
    message: str
    user_id: UUID | None = None
    user_name: str | None = None


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
    user_name: str | None = None
    user_email: str | None = None
    url: str
    is_main: bool
    status: str
    face_verified: bool = False
    created_at: str | None = None


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
    reject_reason: str | None = None
    face_verified: bool = False
    order: int
    created_at: str | None = None


# Message moderation
class AdminMessageDecryptResponse(BaseModel):
    message_id: str
    sender_id: str
    receiver_id: str
    chat_id: str
    content: str
    sent_at: str | None = None


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
    sent_at: str | None = None
    report_reason: str
    report_description: str | None = None


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
    target_type: str | None = None
    target_id: UUID | None = None
    ip_address: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminLogListResponse(BaseModel):
    logs: list[AdminLogEntry]
    total: int
    page: int
    page_size: int
