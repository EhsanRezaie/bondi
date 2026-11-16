import uuid

from sqlalchemy import Column, Date, ForeignKey, SmallInteger, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DailyLimit(Base):
    __tablename__ = "daily_limits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)  # Tehran date (UTC+3:30)

    # Usage counters (Business Rules #1 and #2)
    likes_used = Column(SmallInteger, default=0)  # max 50 for free users
    chats_used = Column(SmallInteger, default=0)  # max 10 for free users

    # Bonus from rewarded ads (Business Rule #3)
    ad_likes_bonus = Column(SmallInteger, default=0)  # +5 per ad watched
    ad_chats_bonus = Column(SmallInteger, default=0)  # +1 per ad watched

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_limits_user_date"),)

    user = relationship("User", back_populates="daily_limits")
