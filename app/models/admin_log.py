import uuid

from sqlalchemy import Column, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class AdminLog(Base):
    __tablename__ = "admin_logs"
    __table_args__ = (
        Index("idx_admin_logs_admin", "admin_id", "created_at"),
        Index("idx_admin_logs_target", "target_type", "target_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    target_type = Column(String(50))
    target_id = Column(UUID(as_uuid=True))
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
