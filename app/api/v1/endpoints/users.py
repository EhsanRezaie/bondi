# app/api/v1/endpoints/users.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select ,delete 
from app.models.user_interest import UserInterest 
from app.models.user_prompt import UserPrompt
from app.models.block import Block
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone  
from app.models.interest import Interest
from app.db.session import get_session
from app.models.user import User
from app.core.deps import get_current_user, get_current_user_db
from app.core.limiter import limiter
from app.services.location_service import LocationService
from app.schemas.user import UserProfileResponse, UserUpdateRequest, LocationTextUpdateRequest, LocationTextUpdateResponse,InterestUpdateRequest,PromptUpdateRequest
from app.schemas.settings import UserSettingsUpdateRequest, UserSettingsResponse
from app.models.user_settings import UserSettings
from app.core.redis import redis_client
from app.core.cache import cache_get, cache_set, key_user_profile, TTL_USER_PROFILE, invalidate_user_cache, invalidate_auth_user
from app.schemas.discover import ProfileResponse
from app.services.profile_service import serialize_profile, haversine_km
import app.core.redis as redis_module

from app.core.logging import get_logger

logger = get_logger("users")

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfileResponse)
@limiter.limit("100/minute")
async def get_me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:

    cache_key = key_user_profile(current_user.id)
    cached = await cache_get(redis_client, cache_key)
    if cached:
        return UserProfileResponse.model_validate(cached)

    # Always reload with relationships loaded
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    response = UserProfileResponse.model_validate(user)
    await cache_set(redis_client, cache_key, response.model_dump(mode='json'), TTL_USER_PROFILE)
    return response


@router.put("/me", response_model=UserProfileResponse)
@limiter.limit("30/minute")
async def update_me(
    request: Request,
    update_data: UserUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """
    Update current user's profile.
    All fields are optional - only provided fields will be updated.
    """
    update_dict = update_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    
    # Lock user row for update to prevent lost-update race conditions
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == current_user.id)
        .with_for_update()
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update profile fields (they are in UserProfile, not User)
    profile = user.profile
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )
    
    # Fields that belong to UserProfile
    profile_fields = ['name', 'bio', 'birth_date', 'gender', 'height', 'weight', 
                  'body_type', 'relationship_status', 'living_situation',
                  'children_status', 'smoking', 'drinking', 'education',
                  'workplace', 'religion', 'ethnicity', 'political_orientation',
                  'sexual_orientation', 'languages']
    
    for field, value in update_dict.items():
        if field in profile_fields:
            setattr(profile, field, value)
        else:
            # Fields that belong to User (like age is computed from birth_date)
            setattr(user, field, value)
    
    # Update last_seen
    user.last_seen_at = datetime.now(timezone.utc)
    
    await session.commit()
    await session.refresh(user)
    
    await invalidate_user_cache(redis_client, current_user.id)
    await invalidate_auth_user(redis_client, current_user.id)

    # Reload with profile, settings, user_interests, and prompts
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()
    
    return UserProfileResponse.model_validate(user)


@router.put("/me/settings", response_model=UserSettingsResponse)
@limiter.limit("30/minute")
async def update_settings(
    request: Request,
    update_data: UserSettingsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Update current user's settings.
    All fields are optional - only provided fields will be updated.
    """
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == current_user.id).with_for_update()
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User settings not found",
        )
    
    update_dict = update_data.model_dump(exclude_unset=True)
    
    # Filter out None values — they mean "don't change"
    update_dict = {k: v for k, v in update_dict.items() if v is not None}
    
    if not update_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    
    for field, value in update_dict.items():
        setattr(settings, field, value)
    
    await session.commit()
    await session.refresh(settings)
    
    await invalidate_user_cache(redis_client, current_user.id)
    await invalidate_auth_user(redis_client, current_user.id)
    
    return settings


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def delete_me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
) -> None:
    """
    Soft delete current user account.
    Sets is_active to False instead of hard delete.
    """
    current_user.is_active = False
    await session.commit()
    await invalidate_user_cache(redis_client, current_user.id)
    await invalidate_auth_user(redis_client, current_user.id)


@router.post("/me/location", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def update_location(
    request: Request,
    lat: float,
    lng: float,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
) -> None:
    """
    Update user's current location (lat/lng).
    Called when app opens or user moves.
    If user hasn't manually set location text, auto-fill country/province/city from coordinates.
    """
    if lat < -90 or lat > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Latitude must be between -90 and 90",
        )
    
    if lng < -180 or lng > 180:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Longitude must be between -180 and 180",
        )
    
    # Update location in UserProfile
    profile = current_user.profile
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )
    
    profile.lat = lat
    profile.lng = lng
    current_user.last_seen_at = datetime.now(timezone.utc)
    
    # Auto-fill location text from coordinates (only if user didn't manually set)
    if not profile.location_manual:
        location_data = await LocationService.reverse_geocode(lat, lng)
        if location_data:
            if location_data.get("country"):
                profile.country = location_data.get("country")
            if location_data.get("province"):
                profile.province = location_data.get("province")
            if location_data.get("city"):
                profile.city = location_data.get("city")
    
    await session.commit()
    await invalidate_user_cache(redis_client, current_user.id)
    await invalidate_auth_user(redis_client, current_user.id)


@router.patch("/me/location-text", response_model=LocationTextUpdateResponse)
@limiter.limit("30/minute")
async def update_location_text(
    request: Request,
    body: LocationTextUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
):
    """
    Update user's location with text fields (country, province, city).
    """
    profile = current_user.profile
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )
    
    # Update fields - no validation needed
    if body.country is not None:
        profile.country = body.country
    if body.province is not None:
        profile.province = body.province
    if body.city is not None:
        profile.city = body.city
    
    # Set location_manual to True if any text field was updated
    if body.province is not None or body.city is not None or body.country is not None:
        if body.province or body.city or body.country:
            profile.location_manual = True
    
    await session.commit()
    await session.refresh(current_user)
    await invalidate_user_cache(redis_client, current_user.id)
    await invalidate_auth_user(redis_client, current_user.id)
    
    return LocationTextUpdateResponse(
        country=profile.country,
        province=profile.province,
        city=profile.city,
        location_manual=profile.location_manual
    )


@router.put("/me/interests", response_model=UserProfileResponse)
@limiter.limit("30/minute")
async def update_interests(
    request: Request,
    update_data: InterestUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """
    Update current user's interests.
    Replaces all existing interests with the new list.
    """
    # Get all interest names from database
    result = await session.execute(
        select(Interest).where(Interest.name.in_(update_data.interests))
    )
    existing_interests = result.scalars().all()
    
    # Check if all provided interests exist
    existing_names = {i.name for i in existing_interests}
    provided_names = set(update_data.interests)
    missing_names = provided_names - existing_names
    
    if missing_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid interests: {', '.join(missing_names)}"
        )
    
    # Lock user row to prevent lost-update race conditions on interests/ prompts
    result = await session.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    )
    locked_user = result.scalar_one_or_none()
    if not locked_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Delete all existing user interests
    await session.execute(
        delete(UserInterest).where(UserInterest.user_id == current_user.id)
    )

    # Create new user interests
    for interest in existing_interests:
        user_interest = UserInterest(
            user_id=current_user.id,
            interest_id=interest.id,
        )
        session.add(user_interest)

    await session.commit()
    await session.refresh(current_user)
    await invalidate_user_cache(redis_client, current_user.id)

    # Reload with profile, settings, user_interests, and prompts
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()
    
    return UserProfileResponse.model_validate(user)


@router.put("/me/prompts", response_model=UserProfileResponse)
@limiter.limit("30/minute")
async def update_prompts(
    request: Request,
    update_data: PromptUpdateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """
    Update current user's prompts.
    Replaces all existing prompts with the new list.
    """
    # Lock user row to prevent lost-update race conditions on prompts
    result = await session.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    )
    locked_user = result.scalar_one_or_none()
    if not locked_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Delete all existing user prompts
    await session.execute(
        delete(UserPrompt).where(UserPrompt.user_id == current_user.id)
    )

    # Create new user prompts
    for prompt_data in update_data.prompts:
        user_prompt = UserPrompt(
            user_id=current_user.id,
            prompt_id=uuid.UUID(prompt_data["prompt_id"]),
            answer=prompt_data["answer"],
        )
        session.add(user_prompt)

    await session.commit()
    await session.refresh(current_user)
    await invalidate_user_cache(redis_client, current_user.id)

    # Reload with profile, settings, user_interests, and prompts
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == current_user.id)
    )
    user = result.scalar_one_or_none()
    
    return UserProfileResponse.model_validate(user)


@router.get("/{user_id}", response_model=ProfileResponse)
@limiter.limit("60/minute")
async def get_public_profile(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    """
    Get another user's full public profile (Badoo-style).
    Used when opening a profile from the chats/likes notification feed.
    Returns 404 if the user is inactive, blocked, or blocked you.
    """
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.photos),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
        )
        .where(User.id == user_id)
    )
    target = result.scalar_one_or_none()

    if not target or not target.is_active or not target.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot view your own profile here",
        )

    i_blocked_them = await session.scalar(
        select(Block).where(
            Block.blocker_id == current_user.id,
            Block.blocked_id == user_id,
        )
    )
    they_blocked_me = await session.scalar(
        select(Block).where(
            Block.blocker_id == user_id,
            Block.blocked_id == current_user.id,
        )
    )
    if i_blocked_them or they_blocked_me:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    distance_km = None
    if current_user.profile is not None and current_user.profile.lat is not None:
        distance_km = haversine_km(
            current_user.profile.lat,
            current_user.profile.lng,
            target.profile.lat,
            target.profile.lng,
        )

    is_online = bool(await redis_module.redis_client.exists(f"online:{user_id}"))

    return await serialize_profile(
        target,
        is_online=is_online,
        distance_km=distance_km,
    )