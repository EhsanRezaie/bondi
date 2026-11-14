# app/schemas/nsfw.py
from pydantic import BaseModel


class NSFWCheckResult(BaseModel):
    """Result of an NSFW check on an image."""

    is_safe: bool
    score: float
    threshold: float


class NSFWMetricsResponse(BaseModel):
    """NSFW detection service metrics."""

    total_checked: int
    total_rejected: int
    reject_rate: float
    enabled: bool
    threshold: float


class NSFWConfigResponse(BaseModel):
    """NSFW service configuration."""

    enabled: bool
    threshold: float
