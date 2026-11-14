"""Face Verification schemas (image-based selfie flow)."""

from datetime import datetime

from pydantic import BaseModel


class VerifyResponse(BaseModel):
    """Response after verification attempt."""

    verified: bool
    message: str
    similarity_score: float | None = None
    mismatched_photo_ids: list[str] = []


class VerificationStatusResponse(BaseModel):
    """Response for verification status check."""

    is_verified: bool
    verified_at: datetime | None = None
    eligible_to_verify: bool
    cooldown_remaining_seconds: int | None = None
