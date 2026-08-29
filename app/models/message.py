# app/models/message.py
import uuid
from sqlalchemy import Column, Text, Boolean, DateTime, ForeignKey, func, Integer, String, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.core.encryption import encrypt_message, decrypt_message
from app.core.logging import get_logger

logger = get_logger("models.message")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index('idx_messages_chat_sent', 'chat_id', 'sent_at'),
        Index('idx_messages_receiver_delivered', 'receiver_id', 'is_delivered', postgresql_where=text("is_delivered = false")),
        Index('idx_messages_receiver_read', 'receiver_id', 'is_read', postgresql_where=text("is_read = false")),
        Index('idx_messages_chat_recent', 'chat_id', 'sent_at', postgresql_where=text("is_deleted_for_all = false")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Client-generated id echoed from the sender so the app can reconcile its
    # optimistic send with the server message (dedup) even when the WebSocket
    # echo races the HTTP response.
    client_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    chat_id = Column(UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE", name="fk_messages_chat_id"), nullable=False)

    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Message content
    message_type = Column(String(20), default="text")  # 'text' | 'photo' | 'voice'
    _content = Column("content", Text, nullable=True)  # Stores encrypted content
    reply_to_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)

    # Media files
    media_url = Column(Text, nullable=True)
    media_duration = Column(Integer, nullable=True)  # for voice messages (seconds)
    media_size = Column(Integer, nullable=True)  # file size in bytes

    # Status tracking (Sent → Delivered → Read)
    is_sent = Column(Boolean, default=True)
    is_delivered = Column(Boolean, default=False)
    is_read = Column(Boolean, default=False)

    # Delete features
    is_deleted_for_sender = Column(Boolean, default=False)
    is_deleted_for_receiver = Column(Boolean, default=False)
    is_deleted_for_all = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Edit features
    is_edited = Column(Boolean, default=False, nullable=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    chat = relationship("Chat", back_populates="messages", foreign_keys=[chat_id])
    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])
    reply_to = relationship("Message", remote_side=[id], foreign_keys=[reply_to_id])

    @property
    def content(self) -> str:
        """
        Decrypt content when accessed.
        """
        if not self._content or not self.chat_id:
            return self._content

        try:
            # Decrypt using chat_id
            return decrypt_message(self._content, str(self.chat_id))
        except Exception as e:
            # Data-integrity failure — never return a half-decrypted value silently.
            logger.error(
                "message_decrypt_failed",
                message_id=str(getattr(self, "id", None)),
                chat_id=str(getattr(self, "chat_id", None)),
                error=str(e),
                exc_info=True,
            )
            return self._content

    @content.setter
    def content(self, value: str):
        """
        Encrypt content before storing.
        Raises on encryption failure rather than storing plaintext.
        """
        if value and self.chat_id:
            self._content = encrypt_message(value, str(self.chat_id))
        else:
            self._content = value

    def get_encrypted_content(self) -> str:
        """
        Get the raw encrypted content (for admin/debugging).
        """
        return self._content

    def get_decrypted_content_for_admin(self) -> str:
        """
        Decrypt content for admin review.
        """
        if not self._content or not self.chat_id:
            return self._content
        return decrypt_message(self._content, str(self.chat_id))