import uuid
from sqlalchemy import Column, DateTime, ForeignKey, SmallInteger, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint("invited_id", name="uq_referral_rewards_invited"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inviter_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    invited_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    inviter_days = Column(SmallInteger, default=3)
    invited_days = Column(SmallInteger, default=3)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    inviter = relationship("User", foreign_keys=[inviter_id], back_populates="referral_rewards_given")
    invited = relationship("User", foreign_keys=[invited_id], back_populates="referral_rewards_received")