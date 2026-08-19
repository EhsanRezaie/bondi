from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

from app.schemas.card import CardProfileResponse


class ProfileResponse(BaseModel):
    """Response schema for user profile in discover — Badoo-style full profile."""
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
    here_for: Optional[str] = None
    pets: Optional[str] = None
    workout_frequency: Optional[str] = None
    zodiac_sign: Optional[str] = None

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
    is_verified: bool
    last_seen_at: Optional[str] = None
    is_online: Optional[bool] = None

    class Config:
        from_attributes = True


class DiscoverResponse(BaseModel):
    """Response for discover endpoint"""
    users: List[CardProfileResponse]
    next_offset: Optional[int] = None
    next_cursor: Optional[str] = None
    total: int


class SwipeRequest(BaseModel):
    """Request for swipe endpoint"""
    user_id: UUID
    direction: str  # 'like' or 'pass'


class SwipeResponse(BaseModel):
    """Response for swipe endpoint"""
    matched: bool
    match_id: Optional[UUID] = None
    likes_remaining_today: Optional[int] = None
    chats_remaining_today: Optional[int] = None
    message: str