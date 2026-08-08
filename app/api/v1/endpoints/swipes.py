from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from datetime import date
from uuid import UUID

from app.core.config import settings
from app.services.reward_service import RewardService
from app.services.notification_service import NotificationService
from app.db.session import get_session
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.swipe import Swipe
from app.models.match import Match
from app.models.block import Block
from app.services.photo_service import PhotoService
from app.models.photo import Photo
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.core.redis import redis_client
from app.core.cache import record_swipe_cache, get_swiped_ids
from app.schemas.discover import SwipeRequest, SwipeResponse
from app.schemas.swipe import SwipeStatsResponse, LikedUsersResponse, LikedUserResponse
from app.services.websocket_manager import websocket_manager

from app.core.logging import get_logger
from sqlalchemy.exc import IntegrityError

logger = get_logger("swipes")

router = APIRouter(prefix="/swipes", tags=["swipes"])


async def get_user_main_photo_url(session: AsyncSession, user_id: UUID) -> str | None:
    """Get user's main approved photo URL (resolved to full URL)"""
    result = await session.execute(
        select(Photo.url, Photo.status).where(
            Photo.user_id == user_id,
            Photo.is_main == True,
            Photo.status == "approved"
        )
    )
    row = result.one_or_none()
    if row:
        return await PhotoService.get_photo_url(row[0], row[1])
    return None


async def _background_match_notification(
    session: AsyncSession,
    current_user_id: UUID,
    target_user_id: UUID,
    match_id: UUID,
    current_user_name: str,
    current_user_age: int,
    target_user_name: str,
    target_user_age: int,
):
    """Run after response is sent: create match notifications and broadcast WebSocket."""
    try:
        notification_service = NotificationService(session)
        await notification_service.notify_match(current_user_id, target_user_id, match_id)

        user1_main_photo_url = await get_user_main_photo_url(session, current_user_id)
        user2_main_photo_url = await get_user_main_photo_url(session, target_user_id)

        user1_data = {
            "id": str(current_user_id),
            "name": current_user_name,
            "age": current_user_age,
            "main_photo_url": user1_main_photo_url,
        }
        user2_data = {
            "id": str(target_user_id),
            "name": target_user_name,
            "age": target_user_age,
            "main_photo_url": user2_main_photo_url,
        }

        await websocket_manager.broadcast_match(
            str(current_user_id),
            str(target_user_id),
            str(match_id),
            user1_data,
            user2_data,
            redis_client,
        )
    except Exception as e:
        logger.error("bg_match_notification_failed", match_id=str(match_id), error=str(e), exc_info=True)


@router.post("", response_model=SwipeResponse)
@limiter.limit("30/minute")
async def swipe(
    request: Request,
    body: SwipeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> SwipeResponse:
    """
    Swipe right (like) or left (pass) on a user.
    
    Rules:
    1. Cannot swipe on yourself
    2. Cannot swipe twice on same user
    3. Free users: limited likes per day (configurable via .env)
    4. Premium users: unlimited likes
    5. If both like each other → create match
    """
    
    # Cannot swipe on yourself
    if body.user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot swipe on yourself"
        )
    
    # Load current user's profile (name, age, is_premium)
    profile_result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == current_user_id)
    )
    current_profile = profile_result.scalar_one_or_none()
    if not current_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found"
        )
    
    # Check if target user exists and is active
    result = await session.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(
            User.id == body.user_id,
            User.is_active == True
        )
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check Redis set first (fast path)
    swiped_ids = await get_swiped_ids(redis_client, current_user_id)
    if str(body.user_id) in swiped_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already swiped on this user"
        )

    # Check if already swiped (DB check for race condition safety)
    existing_result = await session.execute(
        select(Swipe).where(
            Swipe.from_user == current_user_id,
            Swipe.to_user == body.user_id
        )
    )
    existing_swipe = existing_result.scalar_one_or_none()

    if existing_swipe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Already swiped {existing_swipe.direction} on this user"
        )
    
    # Check daily like limit using RewardService
    reward_service = RewardService(session)
    likes_remaining = None
    chats_remaining = None

    if body.direction == "like":
        can_like = await reward_service.consume_like(current_user_id, current_profile.is_premium)

        if not can_like:
            remaining = await reward_service.get_remaining_likes(current_user_id, current_profile.is_premium)
            if remaining == 0:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Daily like limit reached ({settings.FREE_USER_DAILY_LIKES} per day). Watch an ad or upgrade to premium."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Daily like limit reached. Watch an ad or upgrade to premium."
                )

    # Always return both remaining counts so the client stays in sync
    likes_result = await reward_service.get_remaining_likes(current_user_id, current_profile.is_premium)
    likes_remaining = likes_result if likes_result != -1 else None

    # Load full user for get_remaining_chats (needs user.profile)
    user_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == current_user_id)
    )
    current_user = user_result.scalar_one()
    chats_result = await reward_service.get_remaining_chats(current_user)
    chats_remaining = chats_result if chats_result != -1 else None
    
    # Create swipe record
    new_swipe = Swipe(
        from_user=current_user_id,
        to_user=body.user_id,
        direction=body.direction,
    )
    session.add(new_swipe)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        logger.warning("swipe_duplicate", from_user=str(current_user_id), to_user=str(body.user_id), error=str(e), exc_info=True)
        existing = await session.execute(
            select(Swipe).where(
                Swipe.from_user == current_user_id,
                Swipe.to_user == body.user_id
            )
        )
        dup = existing.scalar_one_or_none()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Already swiped {dup.direction} on this user"
            )
        raise

    # Record swipe in Redis set for fast discover exclusion
    await record_swipe_cache(redis_client, current_user_id, body.user_id)
    
    # Send like notification (only if recipient is premium)
    if body.direction == "like":
        notification_service = NotificationService(session)
        await notification_service.notify_like(
            liker_id=current_user_id,
            liked_user_id=target_user.id,
            liker_name=current_profile.name,
            liker_age=current_profile.age
        )
    
    # Check for match (if both liked each other)
    matched = False
    match_id = None
    
    if body.direction == "like":
        # Check if target user liked current user
        mutual_result = await session.execute(
            select(Swipe).where(
                Swipe.from_user == body.user_id,
                Swipe.to_user == current_user_id,
                Swipe.direction == "like"
            )
        )
        mutual_swipe = mutual_result.scalar_one_or_none()
        
        if mutual_swipe:
            # Create match
            try:
                new_match = Match(
                    user1_id=current_user_id,
                    user2_id=body.user_id,
                    is_active=True,
                )
                session.add(new_match)
                await session.flush()
            except IntegrityError as e:
                await session.rollback()
                logger.warning("match_duplicate", user1=str(current_user_id), user2=str(body.user_id), error=str(e), exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Already matched with this user",
                )
            
            matched = True
            match_id = new_match.id
            
            # Offload match notification + WebSocket broadcast to background
            background_tasks.add_task(
                _background_match_notification,
                session=session,
                current_user_id=current_user_id,
                target_user_id=target_user.id,
                match_id=new_match.id,
                current_user_name=current_profile.name,
                current_user_age=current_profile.age,
                target_user_name=target_user.profile.name,
                target_user_age=target_user.profile.age,
            )
    
    await session.commit()
    
    return SwipeResponse(
        matched=matched,
        match_id=match_id,
        likes_remaining_today=likes_remaining,
        chats_remaining_today=chats_remaining,
        message="Swiped successfully" + (" You matched!" if matched else "")
    )


@router.get("/stats", response_model=SwipeStatsResponse)
@limiter.limit("30/minute")
async def get_swipe_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get swipe statistics for current user"""
    
    reward_service = RewardService(session)
    stats = await reward_service.get_daily_stats(current_user)
    
    # Count total likes sent
    total_likes_result = await session.execute(
        select(func.count()).where(
            Swipe.from_user == current_user.id,
            Swipe.direction == "like"
        )
    )
    total_likes = total_likes_result.scalar()
    
    # Count total passes sent
    total_passes_result = await session.execute(
        select(func.count()).where(
            Swipe.from_user == current_user.id,
            Swipe.direction == "pass"
        )
    )
    total_passes = total_passes_result.scalar()
    
    # Count matches
    matches_result = await session.execute(
        select(func.count()).where(
            or_(
                Match.user1_id == current_user.id,
                Match.user2_id == current_user.id
            ),
            Match.is_active == True
        )
    )
    total_matches = matches_result.scalar()
    
    return {
        "daily_likes_remaining": stats["likes_remaining_today"],
        "is_unlimited": stats["is_premium"],
        "total_likes_sent": total_likes,
        "total_passes_sent": total_passes,
        "total_matches": total_matches,
        "ads_watched_today": stats["ads_watched_today"],
        "max_ads_per_day": stats["max_ads_per_day"],
    }


@router.get("/liked", response_model=LikedUsersResponse)
@limiter.limit("60/minute")
async def get_liked_users(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> LikedUsersResponse:
    """
    Get paginated list of users the current user has liked (swiped right on).
    
    Returns users sorted by most recent swipe first.
    Excludes blocked users and inactive users.
    """
    
    # Users this user has liked
    liked_user_ids = select(Swipe.to_user).where(
        Swipe.from_user == current_user_id,
        Swipe.direction == "like"
    ).subquery()
    
    # Blocked by current user
    blocked_by_me = select(Block.blocked_id).where(
        Block.blocker_id == current_user_id
    ).subquery()
    
    # Users who blocked current user
    blocked_me = select(Block.blocker_id).where(
        Block.blocked_id == current_user_id
    ).subquery()
    
    # Get total count first
    count_query = select(func.count()).select_from(
        select(User.id)
        .join(UserProfile, User.id == UserProfile.user_id)
        .where(
            User.id.in_(select(liked_user_ids.c.to_user)),
            User.is_active == True,
            User.id.not_in(select(blocked_by_me.c.blocked_id)),
            User.id.not_in(select(blocked_me.c.blocker_id)),
        )
        .subquery()
    )
    total = await session.scalar(count_query)
    
    # Get paginated results with profile data and swipe timestamp
    query = (
        select(User, UserProfile, Swipe.created_at)
        .join(UserProfile, User.id == UserProfile.user_id)
        .join(Swipe, Swipe.to_user == User.id)
        .where(
            Swipe.from_user == current_user_id,
            Swipe.direction == "like",
            User.is_active == True,
            User.id.not_in(select(blocked_by_me.c.blocked_id)),
            User.id.not_in(select(blocked_me.c.blocker_id)),
        )
        .order_by(Swipe.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    result = await session.execute(query)
    rows = result.all()

    # Resolve online status for the page (bulk Redis lookup)
    from app.services.websocket_manager import websocket_manager
    online_map = {}
    if rows:
        online_map = await websocket_manager.get_online_status_bulk(
            [str(r[0].id) for r in rows], redis_client
        )

    users = []
    for user, profile, swiped_at in rows:
        # Get main photo URL
        main_photo_url = await get_user_main_photo_url(session, user.id)
        
        users.append({
            "id": user.id,
            "name": profile.name,
            "age": profile.age,
            "main_photo_url": main_photo_url,
            "is_premium": profile.is_premium,
            "is_verified": user.phone_verified if user.phone_verified is not None else False,
            "is_online": online_map.get(str(user.id), False),
            "last_seen_at": user.last_seen_at,
            "swiped_at": swiped_at,
        })
    
    next_offset = offset + limit if offset + limit < total else None
    
    return LikedUsersResponse(
        users=users,
        total=total,
        next_offset=next_offset,
    )


@router.get("/likers", response_model=LikedUsersResponse)
@limiter.limit("60/minute")
async def get_likers(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> LikedUsersResponse:
    """
    Get paginated list of users who liked (swiped right on) the current user.
    
    Excludes:
    - Users already matched with
    - Blocked users (both directions)
    - Inactive users
    
    Returns users sorted by most recent like first.
    """
    
    # Users who liked current user
    liker_ids = select(Swipe.from_user).where(
        Swipe.to_user == current_user_id,
        Swipe.direction == "like"
    ).subquery()
    
    # Matched users (both directions)
    matched_as_user1 = select(Match.user2_id).where(
        Match.user1_id == current_user_id,
        Match.is_active == True
    )
    matched_as_user2 = select(Match.user1_id).where(
        Match.user2_id == current_user_id,
        Match.is_active == True
    )
    matched_user_ids = matched_as_user1.union(matched_as_user2).subquery()
    
    # Blocked by current user
    blocked_by_me = select(Block.blocked_id).where(
        Block.blocker_id == current_user_id
    ).subquery()
    
    # Users who blocked current user
    blocked_me = select(Block.blocker_id).where(
        Block.blocked_id == current_user_id
    ).subquery()
    
    # Get total count
    count_query = select(func.count()).select_from(
        select(User.id)
        .join(UserProfile, User.id == UserProfile.user_id)
        .where(
            User.id.in_(select(liker_ids.c.from_user)),
            User.is_active == True,
            User.id.not_in(select(matched_user_ids.c.user2_id)),
            User.id.not_in(select(blocked_by_me.c.blocked_id)),
            User.id.not_in(select(blocked_me.c.blocker_id)),
        )
        .subquery()
    )
    total = await session.scalar(count_query)
    
    # Get paginated results
    query = (
        select(User, UserProfile, Swipe.created_at)
        .join(UserProfile, User.id == UserProfile.user_id)
        .join(Swipe, Swipe.from_user == User.id)
        .where(
            Swipe.to_user == current_user_id,
            Swipe.direction == "like",
            User.is_active == True,
            User.id.not_in(select(matched_user_ids.c.user2_id)),
            User.id.not_in(select(blocked_by_me.c.blocked_id)),
            User.id.not_in(select(blocked_me.c.blocker_id)),
        )
        .order_by(Swipe.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    result = await session.execute(query)
    rows = result.all()

    # Resolve online status for the page (bulk Redis lookup)
    from app.services.websocket_manager import websocket_manager
    online_map = {}
    if rows:
        online_map = await websocket_manager.get_online_status_bulk(
            [str(r[0].id) for r in rows], redis_client
        )

    users = []
    for user, profile, swiped_at in rows:
        main_photo_url = await get_user_main_photo_url(session, user.id)
        
        users.append({
            "id": user.id,
            "name": profile.name,
            "age": profile.age,
            "main_photo_url": main_photo_url,
            "is_premium": profile.is_premium,
            "is_verified": user.phone_verified if user.phone_verified is not None else False,
            "is_online": online_map.get(str(user.id), False),
            "last_seen_at": user.last_seen_at,
            "swiped_at": swiped_at,
        })
    
    next_offset = offset + limit if offset + limit < total else None
    
    return LikedUsersResponse(
        users=users,
        total=total,
        next_offset=next_offset,
    )