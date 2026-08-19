"""Face Verification schemas (image-based selfie flow)."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class VerifyResponse(BaseModel):
    """Response after verification attempt."""
    verified: bool
    message: str
    similarity_score: Optional[float] = None


class VerificationStatusResponse(BaseModel):
    """Response for verification status check."""
    is_verified: bool
    verified_at: Optional[datetime] = None
    eligible_to_verify: bool
    cooldown_remaining_seconds: Optional[int] = None
