import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_block_blocker_blocked"),
        Index("idx_blocks_blocker", "blocker_id"),
        Index("idx_blocks_blocked", "blocked_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blocker_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    blocker = relationship("User", foreign_keys=[blocker_id])
    blocked = relationship("User", foreign_keys=[blocked_id])
