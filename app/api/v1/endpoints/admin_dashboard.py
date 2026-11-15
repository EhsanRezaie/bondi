from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.report import Report
from app.models.swipe import Swipe
from app.models.ticket import Ticket
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.dashboard import (
    ActivityStatsResponse,
    DashboardOverviewResponse,
    ReportStatsResponse,
    TicketStatsResponse,
    UserGrowthResponse,
)

logger = get_logger("admin_dashboard")

router = APIRouter(prefix="/admin/dashboard", tags=["admin"])


@router.get("", response_model=DashboardOverviewResponse)
@limiter.limit("30/minute")
async def admin_dashboard_overview(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get dashboard overview statistics"""

    now = datetime.now(UTC)

    today_start = datetime(now.year, now.month, now.day)
    week_ago = now - timedelta(days=7)

    # Total users
    total_users_result = await session.execute(select(func.count()).select_from(User))
    total_users = total_users_result.scalar() or 0

    # Active today (users with last_seen_at today)
    active_today_result = await session.execute(
        select(func.count()).where(User.last_seen_at >= today_start)
    )
    active_today = active_today_result.scalar() or 0

    # New users today
    new_today_result = await session.execute(
        select(func.count()).where(User.created_at >= today_start)
    )
    new_users_today = new_today_result.scalar() or 0

    # New users this week
    new_week_result = await session.execute(select(func.count()).where(User.created_at >= week_ago))
    new_users_this_week = new_week_result.scalar() or 0

    # Premium users
    premium_users_result = await session.execute(
        select(func.count())
        .select_from(User)
        .join(User.profile)
        .where(UserProfile.premium_until > now)
    )
    premium_users = premium_users_result.scalar() or 0

    premium_percentage = (premium_users / total_users * 100) if total_users > 0 else 0

    # Swipes today
    swipes_today_result = await session.execute(
        select(func.count()).where(Swipe.created_at >= today_start)
    )
    total_swipes_today = swipes_today_result.scalar() or 0

    # Matches today
    matches_today_result = await session.execute(
        select(func.count()).where(Match.matched_at >= today_start)
    )
    total_matches_today = matches_today_result.scalar() or 0

    # Messages today
    messages_today_result = await session.execute(
        select(func.count()).where(Message.sent_at >= today_start)
    )
    total_messages_today = messages_today_result.scalar() or 0

    # Pending photos
    pending_photos_result = await session.execute(
        select(func.count()).where(Photo.status == "pending")
    )
    pending_photos = pending_photos_result.scalar() or 0

    # Pending reports
    pending_reports_result = await session.execute(
        select(func.count()).where(Report.status == "pending")
    )
    pending_reports = pending_reports_result.scalar() or 0

    # Open tickets
    open_tickets_result = await session.execute(select(func.count()).where(Ticket.status == "open"))
    open_tickets = open_tickets_result.scalar() or 0

    return DashboardOverviewResponse(
        total_users=total_users,
        active_today=active_today,
        new_users_today=new_users_today,
        new_users_this_week=new_users_this_week,
        premium_users=premium_users,
        premium_percentage=round(premium_percentage, 2),
        total_swipes_today=total_swipes_today,
        total_matches_today=total_matches_today,
        total_messages_today=total_messages_today,
        pending_photos=pending_photos,
        pending_reports=pending_reports,
        open_tickets=open_tickets,
    )


@router.get("/stats/users", response_model=UserGrowthResponse)
@limiter.limit("30/minute")
async def admin_user_growth_stats(
    request: Request,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get user growth chart data for last N days"""

    # Two grouped queries for the whole window (no per-day queries). Same
    # semantics as the previous per-day counts: day boundaries evaluated in
    # the DB session timezone, zero days filled below.
    today = date.today()
    window_start = datetime.combine(today - timedelta(days=days - 1), time.min)
    window_end = datetime.combine(today + timedelta(days=1), time.min)

    async def _counts_by_day(column):
        rows = await session.execute(
            select(func.date(column).label("day"), func.count())
            .where(column >= window_start, column < window_end)
            .group_by(func.date(column))
        )
        return {row[0]: row[1] for row in rows.all()}

    new_by_day = await _counts_by_day(User.created_at)
    active_by_day = await _counts_by_day(User.last_seen_at)

    labels = []
    new_users = []
    active_users = []

    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.isoformat())
        new_users.append(new_by_day.get(day, 0))
        active_users.append(active_by_day.get(day, 0))

    return UserGrowthResponse(labels=labels, new_users=new_users, active_users=active_users)


@router.get("/stats/activity", response_model=ActivityStatsResponse)
@limiter.limit("30/minute")
async def admin_activity_stats(
    request: Request,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get activity chart data for last N days"""

    labels = []
    swipes_data = []
    matches_data = []
    messages_data = []

    # Three grouped queries for the whole window (no per-day queries), same
    # day-boundary semantics as before; zero days filled below.
    today = date.today()
    window_start = datetime.combine(today - timedelta(days=days - 1), time.min)
    window_end = datetime.combine(today + timedelta(days=1), time.min)

    async def _counts_by_day(column):
        rows = await session.execute(
            select(func.date(column).label("day"), func.count())
            .where(column >= window_start, column < window_end)
            .group_by(func.date(column))
        )
        return {row[0]: row[1] for row in rows.all()}

    swipes_by_day = await _counts_by_day(Swipe.created_at)
    matches_by_day = await _counts_by_day(Match.matched_at)
    messages_by_day = await _counts_by_day(Message.sent_at)

    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.isoformat())
        swipes_data.append(swipes_by_day.get(day, 0))
        matches_data.append(matches_by_day.get(day, 0))
        messages_data.append(messages_by_day.get(day, 0))

    return ActivityStatsResponse(
        labels=labels, swipes=swipes_data, matches=matches_data, messages=messages_data
    )


@router.get("/stats/reports", response_model=ReportStatsResponse)
@limiter.limit("30/minute")
async def admin_report_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get report statistics"""

    pending_result = await session.execute(select(func.count()).where(Report.status == "pending"))
    reviewed_result = await session.execute(select(func.count()).where(Report.status == "reviewed"))
    action_result = await session.execute(
        select(func.count()).where(Report.status == "action_taken")
    )

    return ReportStatsResponse(
        pending=pending_result.scalar() or 0,
        reviewed=reviewed_result.scalar() or 0,
        action_taken=action_result.scalar() or 0,
    )


@router.get("/stats/tickets", response_model=TicketStatsResponse)
@limiter.limit("30/minute")
async def admin_ticket_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get ticket statistics"""

    open_result = await session.execute(select(func.count()).where(Ticket.status == "open"))
    progress_result = await session.execute(
        select(func.count()).where(Ticket.status == "in_progress")
    )
    closed_result = await session.execute(select(func.count()).where(Ticket.status == "closed"))

    return TicketStatsResponse(
        open=open_result.scalar() or 0,
        in_progress=progress_result.scalar() or 0,
        closed=closed_result.scalar() or 0,
    )
