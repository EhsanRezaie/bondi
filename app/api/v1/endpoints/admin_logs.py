# app/api/v1/endpoints/admin_logs.py
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.db.session import get_session
from app.core.deps import get_admin_user, AdminIdentity
from app.models.admin_log import AdminLog
from app.schemas.admin import AdminLogEntry, AdminLogListResponse

from app.core.logging import get_logger

logger = get_logger("admin_logs")

router = APIRouter(prefix="/admin/logs", tags=["admin"])


@router.get("", response_model=AdminLogListResponse)
async def admin_list_logs(
    request: Request,
    admin_id: Optional[str] = Query(None, description="Filter by admin username"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    target_type: Optional[str] = Query(None, description="Filter by target type"),
    target_id: Optional[UUID] = Query(None, description="Filter by target id"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    admin: AdminIdentity = Depends(get_admin_user),
) -> AdminLogListResponse:
    """Admin: list audit log entries with filters + pagination."""
    query = select(AdminLog)
    count_query = select(func.count(AdminLog.id))

    if admin_id:
        query = query.where(AdminLog.admin_id == admin_id)
        count_query = count_query.where(AdminLog.admin_id == admin_id)
    if action:
        query = query.where(AdminLog.action == action)
        count_query = count_query.where(AdminLog.action == action)
    if target_type:
        query = query.where(AdminLog.target_type == target_type)
        count_query = count_query.where(AdminLog.target_type == target_type)
    if target_id:
        query = query.where(AdminLog.target_id == target_id)
        count_query = count_query.where(AdminLog.target_id == target_id)
    if from_date:
        query = query.where(AdminLog.created_at >= from_date)
        count_query = count_query.where(AdminLog.created_at >= from_date)
    if to_date:
        query = query.where(AdminLog.created_at <= to_date)
        count_query = count_query.where(AdminLog.created_at <= to_date)

    total = (await session.execute(count_query)).scalar() or 0

    result = await session.execute(
        query.order_by(AdminLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    return AdminLogListResponse(
        logs=[
            AdminLogEntry(
                id=log.id,
                admin_id=log.admin_id,
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                ip_address=log.ip_address,
                created_at=log.created_at,
            )
            for log in logs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )