from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, Float
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import JSONB
from datetime import timedelta, date

from app.db.session import get_session
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_interest import UserInterest
from app.models.interest import Interest
from app.models.photo import Photo
from app.models.block import Block
from app.models.swipe import Swipe
from app.core.deps import get_current_user
import app.core.redis as redis_module
from app.core.limiter import limiter
from app.schemas.search import SearchResponse
from app.services.profile_service import serialize_card
from app.utils.geo import fuzz_distance

from app.core.logging import get_logger

logger = get_logger("search")

router = APIRouter(prefix="/search", tags=["search"])


def haversine_distance(lat1, lng1, lat2_col, lng2_col):
    """Haversine distance as SQL expression. Returns km."""
    cos_val = (
        func.cos(func.radians(lat1)) * func.cos(func.radians(lat2_col)) *
        func.cos(func.radians(lng2_col) - func.radians(lng1)) +
        func.sin(func.radians(lat1)) * func.sin(func.radians(lat2_col))
    )
    return 6371 * func.acos(func.least(1.0, func.greatest(-1.0, cos_val)))


@router.get("", response_model=SearchResponse)
@limiter.limit("60/minute")
async def search_users(
    request: Request,
    age_min: int = Query(18, ge=18, le=100),
    age_max: int = Query(100, ge=18, le=100),
    distance_km: int = Query(None, ge=1, le=500),
    gender: str = Query(None, pattern="^(male|female)$"),
    height_min: int = Query(None, ge=50, le=250),
    height_max: int = Query(None, ge=50, le=250),
    weight_min: int = Query(None, ge=30, le=300),
    weight_max: int = Query(None, ge=30, le=300),
    has_photos: bool = Query(None),
    is_verified: bool = Query(None),
    country: str = Query(None, max_length=100),
    province: str = Query(None, max_length=100),
    city: str = Query(None, max_length=100),
    religion: str = Query(None, max_length=50),
    ethnicity: str = Query(None, max_length=50),
    relationship_status: str = Query(None, max_length=50),
    body_type: str = Query(None, max_length=50),
    education: str = Query(None, max_length=50),
    smoking: str = Query(None, max_length=50),
    drinking: str = Query(None, max_length=50),
    political_orientation: str = Query(None, max_length=50),
    languages: str = Query(None, max_length=200),
    interests: str = Query(None, max_length=500),
    sort_by: str = Query("recent", pattern="^(recent|distance|age|name|last_seen)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SearchResponse:
    """
    Search users with advanced filters.
    Filters, distance, sort, and pagination all happen at the database level.
    """

    if not current_user.profile:
        return SearchResponse(users=[], total=0, next_offset=None)

    current_profile = current_user.profile

    today = date.today()
    max_birth_date = today - timedelta(days=age_min * 365)
    min_birth_date = today - timedelta(days=(age_max + 1) * 365)

    blocked_user_ids = select(Block.blocked_id).where(Block.blocker_id == current_user.id)
    blocked_by_user_ids = select(Block.blocker_id).where(Block.blocked_id == current_user.id)

    query = select(User).options(
        selectinload(User.profile),
        selectinload(User.settings),
        selectinload(User.photos),
    ).where(
        User.is_active == True,
        User.id != current_user.id,
        User.id.not_in(blocked_user_ids),
        User.id.not_in(blocked_by_user_ids),
    )

    query = query.join(UserProfile, User.id == UserProfile.user_id)

    query = query.where(UserProfile.birth_date.between(min_birth_date, max_birth_date))

    if gender:
        query = query.where(UserProfile.gender == gender)
    if height_min:
        query = query.where(UserProfile.height >= height_min)
    if height_max:
        query = query.where(UserProfile.height <= height_max)
    if weight_min:
        query = query.where(UserProfile.weight >= weight_min)
    if weight_max:
        query = query.where(UserProfile.weight <= weight_max)
    if is_verified is not None:
        query = query.where(UserProfile.is_verified == is_verified)
    if country:
        query = query.where(UserProfile.country == country)
    if province:
        query = query.where(UserProfile.province == province)
    if city:
        query = query.where(UserProfile.city == city)
    if religion:
        query = query.where(UserProfile.religion == religion)
    if ethnicity:
        query = query.where(UserProfile.ethnicity == ethnicity)
    if relationship_status:
        query = query.where(UserProfile.relationship_status == relationship_status)
    if body_type:
        query = query.where(UserProfile.body_type == body_type)
    if education:
        query = query.where(UserProfile.education == education)
    if smoking:
        query = query.where(UserProfile.smoking == smoking)
    if drinking:
        query = query.where(UserProfile.drinking == drinking)
    if political_orientation:
        query = query.where(UserProfile.political_orientation == political_orientation)

    if languages:
        lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]
        if lang_list:
            query = query.where(
                UserProfile.languages.cast(JSONB).contains(lang_list)
            )

    if interests:
        interest_list = [i.strip() for i in interests.split(",") if i.strip()]
        if interest_list:
            interest_count_subquery = (
                select(
                    UserInterest.user_id,
                    func.count(UserInterest.interest_id).label('match_count')
                )
                .join(Interest, UserInterest.interest_id == Interest.id)
                .where(Interest.name.in_(interest_list))
                .group_by(UserInterest.user_id)
                .having(func.count(UserInterest.interest_id) == len(interest_list))
                .subquery()
            )
            query = query.where(
                User.id.in_(
                    select(interest_count_subquery.c.user_id)
                )
            )

    if has_photos is not None:
        if has_photos:
            query = query.where(
                select(func.count(Photo.id))
                .where(Photo.user_id == User.id, Photo.status == "approved")
                .as_scalar() > 0
            )
        else:
            query = query.where(
                select(func.count(Photo.id))
                .where(Photo.user_id == User.id, Photo.status == "approved")
                .as_scalar() == 0
            )

    has_coords = current_profile.lat is not None and current_profile.lng is not None

    if has_coords:
        distance_expr = haversine_distance(
            current_profile.lat, current_profile.lng,
            UserProfile.lat, UserProfile.lng
        )
        if distance_km:
            query = query.where(distance_expr <= distance_km)
        query = query.add_columns(distance_expr.label("distance_km"))
    else:
        query = query.add_columns(func.cast(None, Float).label("distance_km"))
        if sort_by == "distance":
            sort_by = "recent"

    if sort_by == "age":
        col = UserProfile.birth_date
        query = query.order_by(col.asc() if sort_order == "desc" else col.desc())
    elif sort_by == "distance" and has_coords:
        col = distance_expr
        if sort_order == "desc":
            query = query.order_by(col.desc().nullslast())
        else:
            query = query.order_by(col.asc().nullslast())
    elif sort_by == "name":
        col = UserProfile.name
        query = query.order_by(col.desc() if sort_order == "desc" else col.asc())
    elif sort_by == "last_seen":
        col = User.last_seen_at
        query = query.order_by(col.desc().nullslast())
    else:
        col = User.created_at
        query = query.order_by(col.desc() if sort_order == "desc" else col.asc())

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    rows = result.all()

    # Batch query swipe directions for current user → all result users
    result_user_ids = [row[0].id for row in rows]
    swipe_map = {}
    if result_user_ids:
        swipe_rows = await session.execute(
            select(Swipe.to_user, Swipe.direction).where(
                Swipe.from_user == current_user.id,
                Swipe.to_user.in_(result_user_ids)
            )
        )
        swipe_map = {row.to_user: row.direction for row in swipe_rows}

    # Batch query Redis for real-time online presence
    online_map = {}
    if result_user_ids:
        pipe = redis_module.redis_client.pipeline()
        for uid in result_user_ids:
            pipe.exists(f"online:{uid}")
        results = await pipe.execute()
        online_map = {str(uid): bool(r) for uid, r in zip(result_user_ids, results)}

    response_users = []
    for row in rows:
        user = row[0]
        distance = row[1]
        profile = user.profile

        if not profile:
            continue

        is_online = online_map.get(str(user.id), False)

        response_users.append(await serialize_card(
            user,
            is_online=is_online,
            distance_km=fuzz_distance(distance),
            is_verified=profile.is_verified,
            current_user_action=swipe_map.get(user.id),
        ))

    return SearchResponse(
        users=response_users,
        total=total,
        next_offset=offset + limit if offset + limit < total else None,
    )
