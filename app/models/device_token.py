import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String, nullable=False)
    platform = Column(String(10), nullable=False)  # "android" / "ios"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="device_tokens")

    __table_args__ = (
        UniqueConstraint("user_id", "token", name="uq_device_token_user_token"),
        Index("idx_device_tokens_user", "user_id"),
    )
