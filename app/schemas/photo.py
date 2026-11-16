# app/schemas/photo.py
from uuid import UUID

from pydantic import BaseModel, Field


class CropData(BaseModel):
    """Crop data for avatar"""

    x: float = 0.0
    y: float = 0.0
    size: float = 300.0
    scale: float = 1.0


class PhotoResponse(BaseModel):
    """Response schema for photo"""

    id: UUID
    user_id: UUID
    url: str
    order: int
    is_main: bool
    status: str  # pending, approved, rejected
    reject_reason: str | None = None
    face_verified: bool
    crop: CropData | None = None  # Crop data for avatar

    class Config:
        from_attributes = True


class PhotoUploadResponse(BaseModel):
    """Response after uploading a photo"""

    id: UUID
    url: str
    status: str
    message: str = "Photo uploaded. Under review by admin."


class PhotoUpdateCropRequest(BaseModel):
    """Update photo crop data"""

    crop: CropData


class PhotoUpdateOrderRequest(BaseModel):
    """Reorder photos"""

    orders: dict[UUID, int]  # photo_id -> new order


class AdminPhotoApproveRequest(BaseModel):
    """Admin approves photo"""

    photo_id: UUID


class AdminPhotoRejectRequest(BaseModel):
    """Admin rejects photo"""

    photo_id: UUID
    reason: str = Field(..., min_length=1, max_length=500)
