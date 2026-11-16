from uuid import UUID

from pydantic import BaseModel

from app.schemas.card import CardProfileResponse


class ProfileResponse(BaseModel):
    """Response schema for user profile in discover — Badoo-style full profile."""

    # Basic
    id: UUID
    name: str
    age: int
    gender: str
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
    education: str | None = None
    workplace: str | None = None
    religion: str | None = None
    ethnicity: str | None = None
    political_orientation: str | None = None
    languages: list[str] | None = None

    # Location
    country: str | None = None
    province: str | None = None
    city: str | None = None
    distance_km: float | None = None

    # Photos
    main_photo_url: str | None = None
    main_photo_thumb_url: str | None = None
    photos: list[str] | None = None

    # Interests & Prompts
    interests: list[str] | None = None
    prompts: list[dict] | None = None

    # Status
    is_premium: bool
    is_verified: bool
    last_seen_at: str | None = None
    is_online: bool | None = None

    class Config:
        from_attributes = True


class DiscoverResponse(BaseModel):
    """Response for discover endpoint"""

    users: list[CardProfileResponse]
    next_offset: int | None = None
    next_cursor: str | None = None
    total: int


class SwipeRequest(BaseModel):
    """Request for swipe endpoint"""

    user_id: UUID
    direction: str  # 'like' or 'pass'


class SwipeResponse(BaseModel):
    """Response for swipe endpoint"""

    matched: bool
    match_id: UUID | None = None
    likes_remaining_today: int | None = None
    chats_remaining_today: int | None = None
    message: str
