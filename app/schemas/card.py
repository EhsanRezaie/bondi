# app/schemas/card.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CardProfileResponse(BaseModel):
    """Slim card payload for discover/search lists.

    Contains only the fields the mobile card widgets render. The full profile
    (bio, photos, interests, prompts, ...) is served by GET /users/{user_id}.
    """

    id: UUID
    name: str
    age: int
    gender: str
    main_photo_url: str | None = None
    main_photo_thumb_url: str | None = None
    distance_km: float | None = None
    is_premium: bool
    is_verified: bool = False
    is_online: bool | None = None
    created_at: datetime | None = None
    current_user_action: str | None = None  # search only ("like"/"pass"); discover always null

    class Config:
        from_attributes = True
