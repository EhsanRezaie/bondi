from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, select
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

    labels = []
    new_users = []
    active_users = []

    for i in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)

        labels.append(day.isoformat())

        # New users on this day
        new_result = await session.execute(
            select(func.count()).where(
                and_(User.created_at >= day_start, User.created_at < day_end)
            )
        )
        new_users.append(new_result.scalar() or 0)

        # Active users on this day (last_seen_at within this day)
        active_result = await session.execute(
            select(func.count()).where(
                and_(User.last_seen_at >= day_start, User.last_seen_at < day_end)
            )
        )
        active_users.append(active_result.scalar() or 0)

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

    for i in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)

        labels.append(day.isoformat())

        # Swipes
        swipes_result = await session.execute(
            select(func.count()).where(
                and_(Swipe.created_at >= day_start, Swipe.created_at < day_end)
            )
        )
        swipes_data.append(swipes_result.scalar() or 0)

        # Matches
        matches_result = await session.execute(
            select(func.count()).where(
                and_(Match.matched_at >= day_start, Match.matched_at < day_end)
            )
        )
        matches_data.append(matches_result.scalar() or 0)

        # Messages
        messages_result = await session.execute(
            select(func.count()).where(
                and_(Message.sent_at >= day_start, Message.sent_at < day_end)
            )
        )
        messages_data.append(messages_result.scalar() or 0)

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
