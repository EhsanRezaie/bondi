from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID
from datetime import datetime

from app.db.session import get_session
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from sqlalchemy.orm import selectinload
from app.models.user import User
from app.models.report import Report
from app.schemas.admin import AdminReportResponse, AdminReportUpdate

from app.core.logging import get_logger
from app.core.timezone import utcnow
from app.services.admin_log_service import log_admin_action

logger = get_logger("admin_reports")

router = APIRouter(prefix="/admin/reports", tags=["admin"])


@router.get("", response_model=list[AdminReportResponse])
@limiter.limit("60/minute")
async def admin_list_reports(
    request: Request,
    status_filter: str = Query(None, pattern="^(pending|reviewed|action_taken)$"),
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: List all reports"""
    
    query = select(Report).order_by(Report.created_at.desc())
    
    if status_filter:
        query = query.where(Report.status == status_filter)
    
    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    reports = result.scalars().all()
    
    response_reports = []
    for report in reports:
        reporter_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == report.reporter_id)
        )
        reporter = reporter_result.scalar_one_or_none()
        
        reported_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == report.reported_id)
        )
        reported = reported_result.scalar_one_or_none()
        
        response_reports.append(AdminReportResponse(
            id=report.id,
            reporter_id=report.reporter_id,
            reporter_name=reporter.profile.name if reporter else "Deleted User",
            reported_id=report.reported_id,
            reported_name=reported.profile.name if reported else "Deleted User",
            reason=report.reason,
            status=report.status,
            admin_note=report.admin_note,
            created_at=report.created_at,
            resolved_at=report.resolved_at
        ))
    
    return response_reports


@router.get("/{report_id}", response_model=AdminReportResponse)
@limiter.limit("60/minute")
async def admin_get_report(
    request: Request,
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get report details"""
    
    result = await session.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    reporter_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == report.reporter_id)
    )
    reporter = reporter_result.scalar_one_or_none()
    
    reported_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == report.reported_id)
    )
    reported = reported_result.scalar_one_or_none()
    
    return AdminReportResponse(
        id=report.id,
        reporter_id=report.reporter_id,
        reporter_name=reporter.profile.name if reporter else "Deleted User",
        reported_id=report.reported_id,
        reported_name=reported.profile.name if reported else "Deleted User",
        reason=report.reason,
        status=report.status,
        admin_note=report.admin_note,
        created_at=report.created_at,
        resolved_at=report.resolved_at
    )


@router.patch("/{report_id}", response_model=AdminReportResponse)
@limiter.limit("30/minute")
async def admin_update_report(
    request: Request,
    report_id: UUID,
    body: AdminReportUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Review report and take action"""
    
    result = await session.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    report.status = body.status
    if body.admin_note is not None:
        report.admin_note = body.admin_note
    
    if body.status in ["reviewed", "action_taken"]:
        report.resolved_at = utcnow()
    
    await session.commit()
    await log_admin_action(str(admin.id), "report_update", "report", report.id, request, session)
    
    reporter_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == report.reporter_id)
    )
    reporter = reporter_result.scalar_one_or_none()
    
    reported_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == report.reported_id)
    )
    reported = reported_result.scalar_one_or_none()
    
    return AdminReportResponse(
        id=report.id,
        reporter_id=report.reporter_id,
        reporter_name=reporter.profile.name if reporter else "Deleted User",
        reported_id=report.reported_id,
        reported_name=reported.profile.name if reported else "Deleted User",
        reason=report.reason,
        status=report.status,
        admin_note=report.admin_note,
        created_at=report.created_at,
        resolved_at=report.resolved_at
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def admin_delete_report(
    request: Request,
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Delete a report"""
    
    result = await session.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    await log_admin_action(str(admin.id), "report_delete", "report", report.id, request, session)
    await session.delete(report)
    await session.commit()