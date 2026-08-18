# app/schemas/card.py
from uuid import UUID
from typing import Optional
from datetime import datetime
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
    main_photo_url: Optional[str] = None
    distance_km: Optional[float] = None
    is_premium: bool
    is_verified: bool = False
    is_online: Optional[bool] = None
    created_at: Optional[datetime] = None
    current_user_action: Optional[str] = None  # search only ("like"/"pass"); discover always null

    class Config:
        from_attributes = True
