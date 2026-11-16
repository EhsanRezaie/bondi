import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Privacy
    hide_last_seen = Column(Boolean, default=False)
    hide_online_status = Column(Boolean, default=False)

    # Notifications
    push_enabled = Column(Boolean, default=True)
    like_notifications = Column(Boolean, default=True)
    match_notifications = Column(Boolean, default=True)
    message_notifications = Column(Boolean, default=True)

    # App
    language = Column(String(10), default="fa")
    dark_mode = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    # Relationships
    user = relationship("User", back_populates="settings")
