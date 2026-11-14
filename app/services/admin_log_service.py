from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.admin_log import AdminLog

logger = get_logger("admin_log")


async def log_admin_action(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: UUID,
    request: Request,
    db: AsyncSession,
):
    """Log an admin action for audit trail."""
    log = AdminLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=request.client.host if request.client else "unknown",
    )
    db.add(log)
    try:
        await db.flush()
    except Exception:
        logger.warning("failed_to_log_admin_action", admin_id=admin_id, action=action)
