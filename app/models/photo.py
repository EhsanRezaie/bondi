import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        Index(
            "idx_photos_user_main", "user_id", "is_main", postgresql_where=text("is_main = true")
        ),
        # Rare rows only: discover/search anti-join "no pending/rejected photos"
        # probes this instead of scanning every photo per candidate (measured
        # ~18% faster discover at 60k users).
        Index(
            "idx_photos_not_approved",
            "user_id",
            postgresql_where=text("status <> 'approved'"),
        ),
        Index("idx_photos_user_id", "user_id", "created_at"),
        Index("idx_photos_status", "status", "created_at"),
        Index("idx_photos_phash", "user_id", "phash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    url = Column(String, nullable=False)
    order = Column(SmallInteger, default=0)
    is_main = Column(Boolean, default=False)

    # Moderation fields
    status = Column(String(20), default="pending")
    reject_reason = Column(Text, nullable=True)
    nsfw_score = Column(Float, nullable=True)

    # Face verification
    face_verified = Column(Boolean, default=False)

    # Perceptual hash (dHash hex) for duplicate-upload detection
    phash = Column(String(64), nullable=True)

    # Add created_at if missing
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="photos")

    # Crop data for avatar
    crop = Column(JSON, nullable=True)
