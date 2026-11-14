# app/schemas/search.py
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.card import CardProfileResponse


class SearchProfileResponse(BaseModel):
    """Response schema for search results — matches discover ProfileResponse shape."""

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
    photos: list[str] | None = None

    # Interests & Prompts
    interests: list[str] | None = None
    prompts: list[dict] | None = None

    # Status
    is_premium: bool
    is_verified: bool = False
    last_seen_at: str | None = None
    is_online: bool | None = None
    current_user_action: str | None = None  # "like", "pass", or null

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    """Response for search endpoint."""

    users: list[CardProfileResponse]
    total: int
    next_offset: int | None = None
    next_cursor: str | None = None  # opaque keyset cursor for the next page


class SearchFilters(BaseModel):
    """Search filters."""

    age_min: int = Field(18, ge=18, le=100)
    age_max: int = Field(100, ge=18, le=100)
    distance_km: int | None = Field(None, ge=1, le=500)
    gender: str | None = Field(None, pattern="^(male|female)$")
    height_min: int | None = Field(None, ge=50, le=250)
    height_max: int | None = Field(None, ge=50, le=250)
    weight_min: int | None = Field(None, ge=30, le=300)
    weight_max: int | None = Field(None, ge=30, le=300)
    has_photos: bool | None = None
    is_verified: bool | None = None
    country: str | None = Field(None, max_length=100)
    province: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)
    religion: str | None = Field(None, max_length=50)
    ethnicity: str | None = Field(None, max_length=50)
    relationship_status: str | None = Field(None, max_length=50)
    body_type: str | None = Field(None, max_length=50)
    education: str | None = Field(None, max_length=50)
    smoking: str | None = Field(None, max_length=50)
    drinking: str | None = Field(None, max_length=50)
    political_orientation: str | None = Field(None, max_length=50)
    here_for: str | None = Field(None, max_length=50)
    pets: str | None = Field(None, max_length=50)
    workout_frequency: str | None = Field(None, max_length=50)
    zodiac_sign: str | None = Field(None, max_length=50)
    children_status: str | None = Field(None, max_length=50)
    languages: str | None = Field(
        None, max_length=200, description="Comma-separated language codes"
    )
    interests: str | None = Field(
        None, max_length=500, description="Comma-separated interest names"
    )
    sort_by: str = Field("recent", pattern="^(recent|distance|age|name|last_seen)$")
    sort_order: str = Field("desc", pattern="^(asc|desc)$")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class BlockResponse(BaseModel):
    """Response for block list."""

    id: UUID
    blocked_user_id: UUID
    blocked_user_name: str | None = None
    blocked_user_age: int | None = None
    main_photo_url: str | None = None
    blocked_at: str

    class Config:
        from_attributes = True
