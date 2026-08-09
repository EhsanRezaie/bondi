from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, Float
from sqlalchemy.orm import selectinload
from datetime import date, timedelta

from app.db.session import get_session
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.swipe import Swipe
from app.models.match import Match
from app.models.block import Block
from app.core.deps import get_current_user
from app.core.limiter import limiter
import app.core.redis as redis_module
from app.core.cache import (
    pop_discover_stack, set_discover_stack, get_swiped_ids,
    DISCOVER_STACK_SIZE,
)
from app.schemas.discover import DiscoverResponse
from app.services.profile_service import serialize_card
from app.utils.geo import fuzz_distance

from app.core.logging import get_logger

logger = get_logger("discover")

router = APIRouter(prefix="/discover", tags=["discover"])


def haversine_distance(lat1, lng1, lat2_col, lng2_col):
    """Haversine distance as SQL expression. Returns km."""
    cos_val = (
        func.cos(func.radians(lat1)) * func.cos(func.radians(lat2_col)) *
        func.cos(func.radians(lng2_col) - func.radians(lng1)) +
        func.sin(func.radians(lat1)) * func.sin(func.radians(lat2_col))
    )
    return 6371 * func.acos(func.least(1.0, func.greatest(-1.0, cos_val)))


@router.get("", response_model=DiscoverResponse)
@limiter.limit("60/minute")
async def discover(
    request: Request,
    age_min: int = Query(18, ge=18, le=100),
    age_max: int = Query(100, ge=18, le=100),
    gender: str = Query(None, pattern="^(male|female)$"),
    distance_km: Optional[int] = Query(None, ge=1, le=500),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DiscoverResponse:
    """
    Discover page for swiping.
    Users you've swiped on (like OR pass) are excluded.
    Users you blocked are excluded.
    Users who blocked you are excluded.
    Users you already matched with are excluded.
    Filters by gender if provided, otherwise shows all genders.
    Distance filter is applied at the database level.
    """

    if not current_user.profile:
        return DiscoverResponse(users=[], total=0, next_offset=None)

    current_profile = current_user.profile

    today = date.today()
    max_birth_date = today - timedelta(days=age_min * 365)
    min_birth_date = today - timedelta(days=(age_max + 1) * 365)

    # --- Swipe deduplication via Redis set (fast path) ---
    swiped_ids = await get_swiped_ids(redis_module.redis_client, current_user.id)

    blocked_user_ids = select(Block.blocked_id).where(
        Block.blocker_id == current_user.id
    ).subquery()

    blocked_by_user_ids = select(Block.blocker_id).where(
        Block.blocked_id == current_user.id
    ).subquery()

    matched_as_user1 = select(Match.user2_id).where(
        Match.user1_id == current_user.id,
        Match.is_active == True
    )
    matched_as_user2 = select(Match.user1_id).where(
        Match.user2_id == current_user.id,
        Match.is_active == True
    )
    matched_user_ids = matched_as_user1.union(matched_as_user2).subquery()

    # Build exclusion list: blocked + matched from DB, swiped from Redis
    exclude_ids = list(swiped_ids) if swiped_ids else []

    query = select(User).options(
        selectinload(User.profile),
        selectinload(User.settings),
        selectinload(User.photos),
    ).join(UserProfile, User.id == UserProfile.user_id).where(
        User.is_active == True,
        User.id != current_user.id,
        UserProfile.birth_date.between(min_birth_date, max_birth_date),
        User.id.not_in(select(blocked_user_ids.c.blocked_id)),
        User.id.not_in(select(blocked_by_user_ids.c.blocker_id)),
        User.id.not_in(select(matched_user_ids.c.user2_id)),
    )

    # Exclude swiped users via Redis set (if populated), else fall back to DB
    if exclude_ids:
        query = query.where(User.id.not_in(exclude_ids))
    else:
        # Redis set empty or unavailable — fall back to DB subquery
        swiped_db_ids = select(Swipe.to_user).where(
            Swipe.from_user == current_user.id
        ).subquery()
        query = query.where(User.id.not_in(select(swiped_db_ids.c.to_user)))

    if gender:
        query = query.where(UserProfile.gender == gender)

    has_coords = current_profile.lat is not None and current_profile.lng is not None

    if has_coords:
        distance_expr = haversine_distance(
            current_profile.lat, current_profile.lng,
            UserProfile.lat, UserProfile.lng
        )
        if distance_km is not None:
            query = query.where(distance_expr <= distance_km)
        query = query.add_columns(distance_expr.label("distance_km"))
    else:
        query = query.add_columns(func.cast(None, Float).label("distance_km"))

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    rows = result.all()

    response_users = []

    # Batch query Redis for real-time online presence
    user_ids = [row[0].id for row in rows if row[0].profile]
    online_map = {}
    if user_ids:
        pipe = redis_module.redis_client.pipeline()
        for uid in user_ids:
            pipe.exists(f"online:{uid}")
        results = await pipe.execute()
        online_map = {str(uid): bool(r) for uid, r in zip(user_ids, results)}

    for row in rows:
        user = row[0]
        distance = row[1]
        if not user.profile:
            continue

        is_online = online_map.get(str(user.id), False)

        response_users.append(await serialize_card(
            user,
            is_online=is_online,
            distance_km=fuzz_distance(distance),
            is_verified=user.profile.is_verified if user.profile.is_verified is not None else False,
        ))

    has_more = offset + limit < total
    next_offset = offset + limit if has_more else None

    return DiscoverResponse(
        users=response_users,
        next_offset=next_offset,
        total=total,
    )
