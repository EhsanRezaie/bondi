import uuid

from sqlalchemy import Column, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class UserPrompt(Base):
    __tablename__ = "user_prompts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    prompt_id = Column(
        UUID(as_uuid=True), ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False
    )
    answer = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "prompt_id", name="uq_user_prompts_user_prompt"),
        Index("idx_user_prompts_user", "user_id"),
    )

    # Relationships
    user = relationship("User", back_populates="prompts")
    prompt = relationship("Prompt", back_populates="user_prompts")
