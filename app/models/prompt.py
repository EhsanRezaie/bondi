import uuid

from sqlalchemy import Boolean, Column, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question = Column(Text, nullable=False)
    category = Column(String(30), nullable=True)  # about_me, lifestyle, etc.
    language = Column(String(5), nullable=False, default="fa")  # 'fa', 'en', etc.
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("question", "language", name="uq_prompts_question_language"),
    )

    # Relationships
    user_prompts = relationship("UserPrompt", back_populates="prompt", cascade="all, delete-orphan")
