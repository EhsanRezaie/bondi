from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from uuid import UUID
from datetime import datetime, timedelta

from app.db.session import get_session
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.models.user import User
from app.models.report import Report
from app.schemas.report import ReportRequest, ReportResponse
import app.core.redis as redis

from app.core.logging import get_logger

logger = get_logger("reports")

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/{user_id}", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def report_user(
    request: Request,
    user_id: UUID,
    body: ReportRequest,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
):
    """Report a user for inappropriate behavior"""
    
    # Cannot report yourself
    if user_id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot report yourself")
    
    # Check if target user exists
    result = await session.execute(
        select(User).where(User.id == user_id)
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already reported this user in last 24 hours
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    existing = await session.execute(
        select(Report).where(
            Report.reporter_id == current_user_id,
            Report.reported_id == user_id,
            Report.created_at > twenty_four_hours_ago
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You have already reported this user recently")

    # Daily report limit (5 per day per user)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily_key = f"reports:{current_user_id}:{today}"
    try:
        pipe = redis.redis_client.pipeline()
        incr_result = pipe.incr(daily_key)
        pipe.expire(daily_key, 86400)
        results = await pipe.execute()
        daily_count = results[0]
        if daily_count > 5:
            raise HTTPException(status_code=429, detail="Report limit reached for today.")
    except HTTPException:
        raise
    except Exception:
        logger.warning("Redis daily report limit check failed, allowing report")

    # Create report
    report = Report(
        reporter_id=current_user_id,
        reported_id=user_id,
        reason=body.reason,
        status="pending"
    )
    session.add(report)
    await session.flush()
    await session.commit()
    
    return ReportResponse(
        id=report.id,
        reported_user_id=user_id,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at
    )


@router.get("/my", response_model=list[ReportResponse])
@limiter.limit("30/minute")
async def get_my_reports(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get all reports submitted by current user"""
    
    result = await session.execute(
        select(Report).where(
            Report.reporter_id == current_user.id
        ).order_by(Report.created_at.desc())
    )
    reports = result.scalars().all()
    
    return [
        ReportResponse(
            id=r.id,
            reported_user_id=r.reported_id,
            reason=r.reason,
            status=r.status,
            created_at=r.created_at
        )
        for r in reports
    ]