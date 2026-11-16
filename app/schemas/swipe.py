from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SwipeStatsResponse(BaseModel):
    """Response schema for swipe statistics."""

    daily_likes_remaining: int
    is_unlimited: bool
    total_likes_sent: int
    total_passes_sent: int
    total_matches: int
    total_messages: int
    ads_watched_today: int
    max_ads_per_day: int


class LikedUserResponse(BaseModel):
    """Response schema for a user who was liked or who liked you."""

    id: UUID
    name: str
    age: int
    main_photo_url: str | None = None
    is_premium: bool = False
    is_verified: bool = False
    is_online: bool = False
    last_seen_at: datetime | None = None
    swiped_at: datetime

    class Config:
        from_attributes = True


class LikedUsersResponse(BaseModel):
    """Paginated response for liked users / likers."""

    users: list[LikedUserResponse]
    total: int
    next_offset: int | None = None
    next_cursor: str | None = None
