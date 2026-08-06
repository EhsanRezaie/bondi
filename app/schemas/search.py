# app/schemas/search.py
from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel, Field

from app.schemas.card import CardProfileResponse


class SearchProfileResponse(BaseModel):
    """Response schema for search results — matches discover ProfileResponse shape."""
    # Basic
    id: UUID
    name: str
    age: int
    gender: str
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
    education: Optional[str] = None
    workplace: Optional[str] = None
    religion: Optional[str] = None
    ethnicity: Optional[str] = None
    political_orientation: Optional[str] = None
    languages: Optional[List[str]] = None

    # Location
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    distance_km: Optional[float] = None

    # Photos
    main_photo_url: Optional[str] = None
    photos: Optional[List[str]] = None

    # Interests & Prompts
    interests: Optional[List[str]] = None
    prompts: Optional[List[dict]] = None

    # Status
    is_premium: bool
    is_verified: bool = False
    last_seen_at: Optional[str] = None
    is_online: Optional[bool] = None
    current_user_action: Optional[str] = None  # "like", "pass", or null

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    """Response for search endpoint."""
    users: List[CardProfileResponse]
    total: int
    next_offset: Optional[int] = None


class SearchFilters(BaseModel):
    """Search filters."""
    age_min: int = Field(18, ge=18, le=100)
    age_max: int = Field(100, ge=18, le=100)
    distance_km: Optional[int] = Field(None, ge=1, le=500)
    gender: Optional[str] = Field(None, pattern="^(male|female)$")
    height_min: Optional[int] = Field(None, ge=50, le=250)
    height_max: Optional[int] = Field(None, ge=50, le=250)
    weight_min: Optional[int] = Field(None, ge=30, le=300)
    weight_max: Optional[int] = Field(None, ge=30, le=300)
    has_photos: Optional[bool] = None
    is_verified: Optional[bool] = None
    country: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=100)
    religion: Optional[str] = Field(None, max_length=50)
    ethnicity: Optional[str] = Field(None, max_length=50)
    relationship_status: Optional[str] = Field(None, max_length=50)
    body_type: Optional[str] = Field(None, max_length=50)
    education: Optional[str] = Field(None, max_length=50)
    smoking: Optional[str] = Field(None, max_length=50)
    drinking: Optional[str] = Field(None, max_length=50)
    political_orientation: Optional[str] = Field(None, max_length=50)
    languages: Optional[str] = Field(None, max_length=200, description="Comma-separated language codes")
    interests: Optional[str] = Field(None, max_length=500, description="Comma-separated interest names")
    sort_by: str = Field("recent", pattern="^(recent|distance|age|name|last_seen)$")
    sort_order: str = Field("desc", pattern="^(asc|desc)$")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class BlockResponse(BaseModel):
    """Response for block list."""
    id: UUID
    blocked_user_id: UUID
    blocked_user_name: str
    blocked_at: str
    
    class Config:
        from_attributes = True