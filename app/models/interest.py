import uuid
from sqlalchemy import Column, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class Interest(Base):
    __tablename__ = "interests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), unique=True, nullable=False)
    category = Column(String(30), nullable=True)  # music, sport, travel, food, etc.
    icon = Column(String(50), nullable=True)

    # Localized display labels, keyed by language code:
    #   {"fa": {"name": "فوتبال", "category": "ورزش و تناسب اندام"},
    #    "en": {"name": "Football", "category": "Sports & Fitness"}}
    # `name`/`category` remain stable keys; the client renders these labels.
    translations = Column(JSON, nullable=True)

    # Relationships
    user_interests = relationship("UserInterest", back_populates="interest", cascade="all, delete-orphan")