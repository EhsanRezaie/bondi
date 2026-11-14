import uuid

from sqlalchemy import Column, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ReviewReward(Base):
    __tablename__ = "review_rewards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Business Rule #8: one reward per user per platform
    platform = Column(String(20), nullable=False)  # 'google_play' | 'bazaar' | 'myket'

    status = Column(String(20), default="pending")  # 'pending' | 'approved' | 'rejected'
    days_granted = Column(SmallInteger, default=3)

    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    verified_at = Column(DateTime(timezone=True), nullable=True)  # set when admin confirms

    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_review_rewards_user_platform"),
    )

    user = relationship("User", back_populates="review_rewards")
