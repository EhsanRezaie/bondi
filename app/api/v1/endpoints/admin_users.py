from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from uuid import UUID
from datetime import datetime, timedelta, timezone, date
from typing import Optional
from app.models.notification import Notification
from app.db.session import get_session
from app.core.deps import get_admin_user
from app.core.limiter import limiter
import app.core.redis as redis_module
from app.core.cache import invalidate_auth_user
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_interest import UserInterest
from app.models.swipe import Swipe
from app.models.match import Match
from app.models.message import Message
from app.models.report import Report
from app.models.subscription import Subscription
from app.schemas.admin import AdminUserResponse, AdminUserUpdate, AdminPremiumGrant, AdminUserListResponse, AdminMessageRequest, AdminMessageResponse, UserActivityEntry, AdminUserPhotoResponse
from app.services.photo_service import PhotoService

from app.core.logging import get_logger
from app.services.admin_log_service import log_admin_action

logger = get_logger("admin_users")

router = APIRouter(prefix="/admin/users", tags=["admin"])

USER_LOAD_OPTIONS = (
    selectinload(User.profile),
    selectinload(User.settings),
    selectinload(User.photos),
    selectinload(User.user_interests).selectinload(UserInterest.interest),
)


async def _build_admin_user_response(
    user: User,
    total_likes_sent: Optional[int] = None,
    total_matches: Optional[int] = None,
    total_messages: Optional[int] = None,
    report_count: Optional[int] = None,
) -> AdminUserResponse:
    profile = user.profile
    photos_resp: list[AdminUserPhotoResponse] = []
    if user.photos:
        for p in sorted(user.photos, key=lambda x: (x.order or 0)):
            photos_resp.append(AdminUserPhotoResponse(
                id=p.id,
                url=await PhotoService.get_photo_url(p.url, p.status),
                is_main=p.is_main,
                status=p.status,
                reject_reason=p.reject_reason,
                face_verified=getattr(p, "face_verified", False),
                order=p.order or 0,
                created_at=p.created_at.isoformat() if p.created_at else None,
            ))
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        phone=user.phone,
        phone_verified=bool(user.phone_verified),
        google_id=user.google_id,
        registration_status=user.registration_status,
        token_version=user.token_version,
        referral_code=user.referral_code,
        referred_by=user.referred_by,
        is_active=user.is_active,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        name=profile.name if profile else "",
        birth_date=profile.birth_date if profile else None,
        age=profile.age if profile else 0,
        gender=profile.gender if profile else None,
        sexual_orientation=profile.sexual_orientation if profile else None,
        bio=profile.bio if profile else None,
        height=profile.height if profile else None,
        weight=profile.weight if profile else None,
        body_type=profile.body_type if profile else None,
        relationship_status=profile.relationship_status if profile else None,
        living_situation=profile.living_situation if profile else None,
        children_status=profile.children_status if profile else None,
        smoking=profile.smoking if profile else None,
        drinking=profile.drinking if profile else None,
        languages=profile.languages if profile else None,
        education=profile.education if profile else None,
        workplace=profile.workplace if profile else None,
        religion=profile.religion if profile else None,
        ethnicity=profile.ethnicity if profile else None,
        political_orientation=profile.political_orientation if profile else None,
        lat=profile.lat if profile else None,
        lng=profile.lng if profile else None,
        country=profile.country if profile else None,
        province=profile.province if profile else None,
        city=profile.city if profile else None,
        location_manual=profile.location_manual if profile else None,
        is_verified=profile.is_verified if profile else None,
        verified_at=profile.verified_at if profile else None,
        is_premium=profile.is_premium if profile else False,
        premium_until=profile.premium_until if profile else None,
        hide_last_seen=user.settings.hide_last_seen if user.settings else False,
        hide_online_status=user.settings.hide_online_status if user.settings else False,
        interests=[ui.interest.name for ui in (user.user_interests or []) if ui.interest],
        photos=photos_resp,
        total_likes_sent=total_likes_sent,
        total_matches=total_matches,
        total_messages=total_messages,
        report_count=report_count,
    )


def _years_ago(years: int, today: date) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb 29
        return today.replace(year=today.year - years, month=2, day=28)


@router.get("", response_model=AdminUserListResponse)
@limiter.limit("120/minute")
async def admin_list_users(
    request: Request,
    search: str = Query(None, description="Search by name, email, phone or bio"),
    id: UUID = Query(None, description="Search by exact user UUID"),
    is_active: bool = Query(None),
    is_premium: bool = Query(None),
    is_verified: bool = Query(None),
    gender: str = Query(None, description="Filter by gender (male/female)"),
    city: str = Query(None, description="Filter by city (case-insensitive)"),
    age_min: int = Query(None, ge=18, le=120, description="Minimum age"),
    age_max: int = Query(None, ge=18, le=120, description="Maximum age"),
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: List all users with full-profile fields and filters"""

    query = select(User).options(*USER_LOAD_OPTIONS)
    joined_profile = False

    def join_profile():
        nonlocal joined_profile
        if not joined_profile:
            return query.join(User.profile)
        return query

    if search:
        query = query.join(User.profile)
        joined_profile = True
        like = f"%{search}%"
        query = query.where(or_(
            UserProfile.name.ilike(like),
            User.email.ilike(like),
            User.phone.ilike(like),
            UserProfile.bio.ilike(like),
        ))

    if id is not None:
        query = query.where(User.id == id)

    if is_active is not None:
        query = query.where(User.is_active == is_active)

    now = datetime.now(timezone.utc)
    if is_premium is not None:
        query = join_profile()
        if is_premium:
            query = query.where(UserProfile.premium_until > now)
        else:
            query = query.where(or_(UserProfile.premium_until.is_(None), UserProfile.premium_until <= now))

    if is_verified is not None:
        query = join_profile()
        query = query.where(UserProfile.is_verified == is_verified)

    if gender:
        query = join_profile()
        query = query.where(UserProfile.gender == gender)

    if city:
        query = join_profile()
        query = query.where(UserProfile.city.ilike(f"%{city}%"))

    if age_min is not None or age_max is not None:
        query = join_profile()
        today = date.today()
        if age_min is not None:
            query = query.where(UserProfile.birth_date <= _years_ago(age_min, today))
        if age_max is not None:
            query = query.where(UserProfile.birth_date >= _years_ago(age_max + 1, today))

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.order_by(User.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    users = result.scalars().unique().all()

    response_users = []
    for user in users:
        response_users.append(await _build_admin_user_response(user))

    return AdminUserListResponse(
        users=response_users,
        total=total or 0,
        next_offset=offset + limit if offset + limit < (total or 0) else None
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
@limiter.limit("60/minute")
async def admin_get_user(
    request: Request,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get user details with stats"""

    result = await session.execute(
        select(User).options(*USER_LOAD_OPTIONS).where(User.id == user_id)
    )
    user = result.scalars().unique().one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get stats
    likes_result = await session.execute(
        select(func.count()).where(Swipe.from_user == user_id, Swipe.direction == "like")
    )
    total_likes = likes_result.scalar() or 0

    matches_result = await session.execute(
        select(func.count()).where(
            or_(
                Match.user1_id == user_id,
                Match.user2_id == user_id
            ),
            Match.is_active == True
        )
    )
    total_matches = matches_result.scalar() or 0

    messages_result = await session.execute(
        select(func.count()).where(
            or_(
                Message.sender_id == user_id,
                Message.receiver_id == user_id
            )
        )
    )
    total_messages = messages_result.scalar() or 0

    reports_result = await session.execute(
        select(func.count()).where(Report.reported_id == user_id)
    )
    report_count = reports_result.scalar() or 0

    return await _build_admin_user_response(
        user,
        total_likes_sent=total_likes,
        total_matches=total_matches,
        total_messages=total_messages,
        report_count=report_count,
    )


@router.patch("/{user_id}", response_model=AdminUserResponse)
@limiter.limit("30/minute")
async def admin_update_user(
    request: Request,
    user_id: UUID,
    body: AdminUserUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Update user (activate/deactivate, etc.)"""

    result = await session.execute(
        select(User).options(*USER_LOAD_OPTIONS).where(User.id == user_id)
    )
    user = result.scalars().unique().one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            user.token_version += 1  # Revoke all tokens

    if body.premium_until is not None and user.profile:
        user.profile.premium_until = body.premium_until

    await session.commit()

    result = await session.execute(
        select(User).options(*USER_LOAD_OPTIONS).where(User.id == user_id)
    )
    user = result.scalars().unique().one_or_none()

    await invalidate_auth_user(redis_module.redis_client, user.id)
    await log_admin_action(str(admin.id), "user_update", "user", user.id, request, session)

    return await _build_admin_user_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def admin_delete_user(
    request: Request,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Hard delete user"""

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await session.delete(user)
    await session.commit()

    await invalidate_auth_user(redis_module.redis_client, user_id)
    await log_admin_action(str(admin.id), "user_delete", "user", user_id, request, session)


@router.post("/{user_id}/premium", response_model=AdminUserResponse)
@limiter.limit("30/minute")
async def admin_grant_premium(
    request: Request,
    user_id: UUID,
    body: AdminPremiumGrant,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Grant premium days to user"""
 
    result = await session.execute(
        select(User).options(selectinload(User.profile), selectinload(User.settings)).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    profile = user.profile
    if not profile:
        raise HTTPException(status_code=500, detail="User profile not found")

    now = datetime.now(timezone.utc)
    if profile.premium_until is None or profile.premium_until < now:
        profile.premium_until = now + timedelta(days=body.days)
    else:
        profile.premium_until = profile.premium_until + timedelta(days=body.days)

    # Create subscription record
    subscription = Subscription(
        user_id=user.id,
        plan=f"{body.days}_days",
        status="active",
        started_at=now,
        expires_at=profile.premium_until,
        source="admin_grant"
    )
    session.add(subscription)

    await session.commit()
    await session.refresh(user)

    await invalidate_auth_user(redis_module.redis_client, user.id)
    await log_admin_action(str(admin.id), "premium_grant", "user", user.id, request, session)

    return AdminUserResponse(
        id=user.id,
        email=user.email,
        name=profile.name if profile else "",
        age=profile.age if profile else 0,
        gender=profile.gender if profile else "unknown",
        is_active=user.is_active,
        is_premium=profile.is_premium,
        premium_until=profile.premium_until,
        phone_verified=user.phone_verified if user.phone_verified is not None else False,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        hide_last_seen=user.settings.hide_last_seen if user.settings else False,
        hide_online_status=user.settings.hide_online_status if user.settings else False
    )


@router.get("/{user_id}/activity", response_model=list[UserActivityEntry])
@limiter.limit("60/minute")
async def admin_get_user_activity(
    request: Request,
    user_id: UUID,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get user activity stats for last N days"""

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get daily activity
    from datetime import date, timedelta

    activity = []
    for i in range(days):
        day = date.today() - timedelta(days=i)

        # Count swipes on this day
        swipes_result = await session.execute(
            select(func.count()).where(
                Swipe.from_user == user_id,
                func.date(Swipe.created_at) == day
            )
        )
        swipes = swipes_result.scalar() or 0

        # Count matches on this day
        matches_result = await session.execute(
            select(func.count()).where(
                or_(
                    Match.user1_id == user_id,
                    Match.user2_id == user_id
                ),
                func.date(Match.matched_at) == day
            )
        )
        matches = matches_result.scalar() or 0

        # Count messages on this day
        messages_result = await session.execute(
            select(func.count()).where(
                or_(
                    Message.sender_id == user_id,
                    Message.receiver_id == user_id
                ),
                func.date(Message.sent_at) == day
            )
        )
        messages = messages_result.scalar() or 0

        activity.append({
            "date": day.isoformat(),
            "swipes": swipes,
            "matches": matches,
            "messages": messages
        })

    return activity


@router.post("/{user_id}/message", response_model=AdminMessageResponse)
@limiter.limit("30/minute")
async def admin_message_user(
    request: Request,
    user_id: UUID,
    body: AdminMessageRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Send a direct message to a specific user (creates notification)"""

    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Create notification for the user
    notification = Notification(
        user_id=user_id,
        type="system",
        title=body.title,
        body=body.message,
        data={"from_admin": True, "admin_id": str(admin.id)},
        is_read=False
    )
    session.add(notification)
    await session.commit()
    await log_admin_action(str(admin.id), "user_message", "user", user_id, request, session)

    return AdminMessageResponse(
        success=True,
        message=f"Message sent to {user.profile.name if user.profile else 'user'}",
        user_id=user_id,
        user_name=user.profile.name if user.profile else None
    )
