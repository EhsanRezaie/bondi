import uuid
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import Column, String, Boolean, SmallInteger, Double, DateTime, Text, Date as SQLDate, ForeignKey, JSON, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        Index('idx_profiles_gender', 'gender'),
        Index('idx_profiles_lat_lng', 'lat', 'lng'),
        Index('idx_profiles_birth_date', 'birth_date'),
        Index('idx_profiles_country', 'country'),
        Index('idx_profiles_province', 'province'),
        Index('idx_profiles_city', 'city'),
        Index('idx_profiles_religion', 'religion'),
        Index('idx_profiles_ethnicity', 'ethnicity'),
        Index('idx_profiles_education', 'education'),
        Index('idx_profiles_body_type', 'body_type'),
        Index('idx_profiles_smoking', 'smoking'),
        Index('idx_profiles_drinking', 'drinking'),
        Index('idx_profiles_relationship_status', 'relationship_status'),
        Index('idx_profiles_height', 'height'),
        Index('idx_profiles_here_for', 'here_for'),
        Index('idx_profiles_pets', 'pets'),
        Index('idx_profiles_workout_frequency', 'workout_frequency'),
        Index('idx_profiles_zodiac_sign', 'zodiac_sign'),
        Index('idx_profiles_is_verified', 'is_verified', postgresql_where=text("is_verified = true")),
        Index('idx_profiles_premium_until', 'premium_until'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Identity
    name = Column(String(100), nullable=True)
    birth_date = Column(SQLDate, nullable=True)
    gender = Column(String(10), nullable=True)
    sexual_orientation = Column(String(20), nullable=True)
    bio = Column(Text, nullable=True)

    # Appearance
    height = Column(SmallInteger, nullable=True)
    weight = Column(SmallInteger, nullable=True)
    body_type = Column(String(20), nullable=True)

    # Lifestyle
    relationship_status = Column(String(20), nullable=True)
    living_situation = Column(String(30), nullable=True)
    children_status = Column(String(30), nullable=True)
    smoking = Column(String(20), nullable=True)
    drinking = Column(String(20), nullable=True)
    here_for = Column(String(30), nullable=True)
    pets = Column(String(30), nullable=True)
    workout_frequency = Column(String(20), nullable=True)
    zodiac_sign = Column(String(20), nullable=True)

    # Background
    languages = Column(JSON, nullable=True)
    education = Column(String(50), nullable=True)
    workplace = Column(String(100), nullable=True)
    religion = Column(String(50), nullable=True)
    ethnicity = Column(String(50), nullable=True)
    political_orientation = Column(String(30), nullable=True)

    # Location
    lat = Column(Double, nullable=True)
    lng = Column(Double, nullable=True)
    country = Column(String(100), nullable=True)
    province = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    location_manual = Column(Boolean, default=False)

    # Verification
    is_verified = Column(Boolean, default=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Premium
    premium_until = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)  # ← اضافه شد
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True)

    # Relationships
    user = relationship("User", back_populates="profile")

    @property
    def age(self) -> int:
        if self.birth_date is None:
            return 0
        today = date.today()
        age = today.year - self.birth_date.year
        if (today.month, today.day) < (self.birth_date.month, self.birth_date.day):
            age -= 1
        return age

    @property
    def is_premium(self) -> bool:
        if self.premium_until is None:
            return False
        return self.premium_until > datetime.now(timezone.utc)

    @property
    def is_profile_complete(self) -> bool:
        return all([
            self.name is not None,
            self.birth_date is not None,
            self.gender is not None,
            self.lat is not None,
            self.lng is not None,
        ])

    def add_premium_days(self, days: int):
        now = datetime.now(timezone.utc)
        if self.premium_until is None or self.premium_until < now:
            self.premium_until = now + timedelta(days=days)
        else:
            self.premium_until = self.premium_until + timedelta(days=days)