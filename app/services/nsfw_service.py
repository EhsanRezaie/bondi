"""
NSFW Photo Detection Service

Uses skin-tone analysis as a lightweight heuristic for NSFW detection.
The classifier is pluggable — swap `_classify_heuristic` for an ML model
when TensorFlow/opennsfw2 becomes available.

Architecture:
  check_image(bytes) -> (is_safe: bool, score: float)
  quarantine_photo(bytes, user_id, photo_id) -> str (object key)

Score: 0.0 = safe, 1.0 = explicit. Threshold configurable via NSFW_THRESHOLD.
"""

import io
import uuid
from typing import Tuple, Optional

import numpy as np
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.services.storage import s3_client

logger = get_logger("nsfw_service")

# Skin-tone HSV ranges (more specific to actual skin)
# Excludes pure red, orange, and overly saturated colors
SKIN_LOWER = np.array([0, 25, 80], dtype=np.uint8)
SKIN_UPPER = np.array([50, 170, 240], dtype=np.uint8)


class NSFWService:
    """Detect potentially NSFW images using skin-tone analysis."""

    def __init__(self):
        self._threshold = getattr(settings, "NSFW_THRESHOLD", 0.8)
        self._enabled = getattr(settings, "NSFW_ENABLED", True)
        self._total_checked = 0
        self._total_rejected = 0

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_metrics(self) -> dict:
        """Return current detection metrics."""
        return {
            "total_checked": self._total_checked,
            "total_rejected": self._total_rejected,
            "reject_rate": (
                round(self._total_rejected / self._total_checked, 4)
                if self._total_checked > 0
                else 0.0
            ),
        }

    async def check_image(self, file_bytes: bytes) -> Tuple[bool, float]:
        """
        Analyze an image for NSFW content.

        Returns:
            (is_safe, score) where score is 0.0 (safe) to 1.0 (explicit)
        """
        if not self._enabled:
            return True, 0.0

        try:
            score = self._classify_heuristic(file_bytes)
            self._total_checked += 1

            is_safe = score < self._threshold

            if not is_safe:
                self._total_rejected += 1

            logger.info(
                "nsfw_check",
                score=round(score, 4),
                threshold=self._threshold,
                safe=is_safe,
            )

            return is_safe, score

        except Exception as e:
            # Fail open — log error but allow the image
            logger.error("nsfw_check_failed", error=str(e))
            self._total_checked += 1
            return True, 0.0

    def _classify_heuristic(self, file_bytes: bytes) -> float:
        """
        Skin-tone based NSFW heuristic.

        High percentage of skin-colored pixels → higher score.
        This is a simple baseline; swap for ML model in production.
        """
        image = Image.open(io.BytesIO(file_bytes))

        # Convert to RGB if needed
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Resize for faster processing
        image.thumbnail((200, 200), Image.Resampling.LANCZOS)

        # Convert to HSV for skin detection
        hsv = np.array(image.convert("HSV"))

        # Create skin mask
        skin_mask = np.all((hsv >= SKIN_LOWER) & (hsv <= SKIN_UPPER), axis=2)

        # Calculate skin percentage
        total_pixels = skin_mask.size
        skin_pixels = np.sum(skin_mask)
        skin_ratio = skin_pixels / total_pixels

        # Map skin ratio to NSFW score
        # < 30% skin → likely safe (0.0-0.3)
        # 30-60% skin → uncertain (0.3-0.7)
        # > 60% skin → likely NSFW (0.7-1.0)
        if skin_ratio < 0.30:
            score = skin_ratio * 1.0  # 0.0 - 0.3
        elif skin_ratio < 0.60:
            score = 0.3 + (skin_ratio - 0.30) * 1.33  # 0.3 - 0.7
        else:
            score = 0.7 + (skin_ratio - 0.60) * 0.75  # 0.7 - 1.0

        return min(score, 1.0)

    async def quarantine_photo(
        self,
        file_bytes: bytes,
        user_id: str,
        photo_id: str,
    ) -> str:
        """
        Save a rejected photo to quarantine for admin review.

        Returns the S3 object key.
        """
        key = f"quarantine/{user_id}/{photo_id}.jpg"

        async with s3_client() as s3:
            await s3.put_object(
                Bucket=settings.S3_PRIVATE_BUCKET,
                Key=key,
                Body=file_bytes,
                ContentType="image/jpeg",
            )

        logger.info("nsfw_quarantine", user_id=user_id, photo_id=photo_id, key=key)
        return key


# Singleton
nsfw_service = NSFWService()
