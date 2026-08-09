# app/services/profile_serializer.py
"""Shared profile serialization used by discover and the public profile endpoint."""
from datetime import datetime, timezone
from typing import Optional
from math import radians, sin, cos, asin, sqrt

from fastapi import HTTPException, status

from app.models.user import User
from app.schemas.discover import ProfileResponse
from app.schemas.card import CardProfileResponse
from app.services.photo_service import PhotoService


def haversine_km(lat1, lng1, lat2, lng2) -> Optional[float]:
    """Distance in km between two lat/lng points. Returns None if coords missing."""
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    )
    return r * 2 * asin(sqrt(min(1.0, a)))


async def serialize_profile(
    user: User,
    is_online: Optional[bool] = None,
    distance_km: Optional[float] = None,
) -> ProfileResponse:
    """Build a ProfileResponse from a User with profile/settings/photos/interests/prompts loaded."""
    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    profile = user.profile
    settings = user.settings

    approved_photos_raw = (
        sorted(
            [p for p in user.photos if p.status == "approved"],
            key=lambda p: p.order,
        )
        if user.photos
        else []
    )
    approved_photos = [
        await PhotoService.get_photo_url(p.url, p.status)
        for p in approved_photos_raw
    ]
    main_photo_url = approved_photos[0] if approved_photos else None

    interests = (
        [ui.interest.name for ui in user.user_interests if ui.interest]
        if user.user_interests
        else []
    )

    prompts = (
        [
            {
                "prompt_id": str(up.prompt_id),
                "question": up.prompt.question,
                "answer": up.answer,
            }
            for up in user.prompts
            if up.prompt
        ]
        if user.prompts
        else []
    )

    hide_last_seen = settings.hide_last_seen if settings else False
    hide_online_status = settings.hide_online_status if settings else False

    resolved_online = False if hide_online_status else bool(is_online)
    last_seen_at = None
    if user.last_seen_at:
        if not hide_last_seen:
            last_seen_at = user.last_seen_at.isoformat()
    elif resolved_online:
        if not hide_last_seen:
            last_seen_at = datetime.now(timezone.utc).isoformat()

    return ProfileResponse(
        id=user.id,
        name=profile.name,
        age=profile.age,
        gender=profile.gender,
        sexual_orientation=profile.sexual_orientation,
        bio=profile.bio,
        height=profile.height,
        weight=profile.weight,
        body_type=profile.body_type,
        relationship_status=profile.relationship_status,
        living_situation=profile.living_situation,
        children_status=profile.children_status,
        smoking=profile.smoking,
        drinking=profile.drinking,
        education=profile.education,
        workplace=profile.workplace,
        religion=profile.religion,
        ethnicity=profile.ethnicity,
        political_orientation=profile.political_orientation,
        languages=profile.languages,
        country=profile.country,
        province=profile.province,
        city=profile.city,
        distance_km=distance_km,
        main_photo_url=main_photo_url,
        photos=approved_photos if approved_photos else None,
        interests=interests if interests else None,
        prompts=prompts if prompts else None,
        is_premium=profile.is_premium,
        is_verified=profile.is_verified if profile.is_verified is not None else False,
        last_seen_at=last_seen_at,
        is_online=resolved_online,
    )


async def serialize_card(
    user: User,
    is_online: Optional[bool] = None,
    distance_km: Optional[float] = None,
    is_verified: Optional[bool] = None,
    current_user_action: Optional[str] = None,
) -> CardProfileResponse:
    """Build a slim CardProfileResponse for discover/search list responses.

    Requires User.profile, User.settings and User.photos to be loaded.
    Unlike serialize_profile, this only resolves the first approved photo and
    skips interests/prompts/photo-list serialization.
    """
    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    profile = user.profile
    settings = user.settings

    hide_online_status = settings.hide_online_status if settings else False
    resolved_online = False if hide_online_status else bool(is_online)

    approved_photos_raw = (
        sorted(
            [p for p in user.photos if p.status == "approved"],
            key=lambda p: p.order,
        )
        if user.photos
        else []
    )
    main_photo_url = None
    if approved_photos_raw:
        first = approved_photos_raw[0]
        main_photo_url = await PhotoService.get_photo_url(first.url, first.status)

    return CardProfileResponse(
        id=user.id,
        name=profile.name,
        age=profile.age,
        gender=profile.gender,
        main_photo_url=main_photo_url,
        distance_km=distance_km,
        is_premium=profile.is_premium,
        is_verified=bool(is_verified) if is_verified is not None else False,
        is_online=resolved_online,
        current_user_action=current_user_action,
    )