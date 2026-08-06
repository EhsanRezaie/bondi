# app/models/chat.py
import uuid
from sqlalchemy import Column, Boolean, DateTime, ForeignKey, func, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        # Only one active chat per pair (regardless of stored order)
        Index(
            'uq_chats_active_pair',
            text("LEAST(initiator_id, recipient_id)"),
            text("GREATEST(initiator_id, recipient_id)"),
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index('idx_chats_initiator_time', 'initiator_id', 'updated_at'),
        Index('idx_chats_recipient_time', 'recipient_id', 'updated_at'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    initiator_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    status = Column(String(20), default="pending", nullable=False)  # 'pending' | 'accepted'
    last_message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL", name="fk_chats_last_message"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    initiator = relationship("User", foreign_keys=[initiator_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    last_message = relationship(
        "Message",
        foreign_keys=[last_message_id],
        uselist=False,
    )
    messages = relationship(
        "Message",
        back_populates="chat",
        foreign_keys="Message.chat_id",
        cascade="all, delete-orphan",
    )