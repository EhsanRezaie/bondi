import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Text, func, Integer, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index('idx_users_is_active', 'is_active', postgresql_where=text("is_active = true")),
        Index('idx_users_registration_status', 'registration_status'),
        Index('idx_users_last_seen', 'last_seen_at'),
        Index('idx_users_deleted_at', 'deleted_at'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    phone_verified = Column(Boolean, default=False)
    email = Column(String(255), unique=True, nullable=True, index=True)

    is_active = Column(Boolean, default=True)
    token_version = Column(Integer, default=1, nullable=False)
    registration_status = Column(String(20), default="phone_pending", nullable=False)

    # Account deletion: set on DELETE /users/me, cleared on restore within the
    # grace window. A Celery sweep hard-deletes the row once past the grace period.
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_reason = Column(String(255), nullable=True)

    referral_code = Column(String(20), unique=True, nullable=True)
    referred_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="user", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    daily_limits = relationship("DailyLimit", back_populates="user", cascade="all, delete-orphan")
    review_rewards = relationship("ReviewReward", back_populates="user", cascade="all, delete-orphan")
    sent_swipes = relationship("Swipe", foreign_keys="Swipe.from_user", back_populates="from_user_rel", cascade="all, delete-orphan")
    received_swipes = relationship("Swipe", foreign_keys="Swipe.to_user", back_populates="to_user_rel", cascade="all, delete-orphan")
    matches_as_user1 = relationship("Match", foreign_keys="Match.user1_id", back_populates="user1")
    matches_as_user2 = relationship("Match", foreign_keys="Match.user2_id", back_populates="user2")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    tickets = relationship("Ticket", back_populates="user", cascade="all, delete-orphan")
    sent_reports = relationship("Report", foreign_keys="Report.reporter_id", back_populates="reporter", cascade="all, delete-orphan")
    received_reports = relationship("Report", foreign_keys="Report.reported_id", back_populates="reported", cascade="all, delete-orphan")
    referral_rewards_given = relationship("ReferralReward", foreign_keys="ReferralReward.inviter_id", back_populates="inviter")
    referral_rewards_received = relationship("ReferralReward", foreign_keys="ReferralReward.invited_id", back_populates="invited")
    user_interests = relationship("UserInterest", back_populates="user", cascade="all, delete-orphan")
    prompts = relationship("UserPrompt", back_populates="user", cascade="all, delete-orphan")
    device_tokens = relationship("DeviceToken", back_populates="user", cascade="all, delete-orphan")
    