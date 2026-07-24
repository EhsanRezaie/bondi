from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request
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
from app.services.photo_service import PhotoService
from app.models.photo import Photo
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.core.redis import redis_client
from app.core.cache import record_swipe_cache
from app.schemas.discover import SwipeRequest, SwipeResponse
from app.schemas.swipe import SwipeStatsResponse
from app.services.websocket_manager import websocket_manager

from app.core.logging import get_logger

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
    )


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
    
    # Check if already swiped
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
    await session.flush()

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
            new_match = Match(
                user1_id=current_user_id,
                user2_id=body.user_id,
                is_active=True,
            )
            session.add(new_match)
            await session.flush()
            
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