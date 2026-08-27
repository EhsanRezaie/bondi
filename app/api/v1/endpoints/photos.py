from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from uuid import UUID
import uuid

from app.core.config import settings
from app.db.session import get_session
from app.models.user import User
from app.models.photo import Photo
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.redis import redis_client
from app.core.cache import invalidate_user_cache
from app.services.photo_service import PhotoService
from app.services.nsfw_service import nsfw_service
from app.services.face_verification_service import face_verification_service
import app.core.redis as redis_module
from app.schemas.photo import (
    PhotoResponse,
    PhotoUploadResponse,
    PhotoUpdateCropRequest,
    PhotoUpdateOrderRequest,
)

from app.core.logging import get_logger

logger = get_logger("photos")

router = APIRouter(prefix="/users/me/photos", tags=["photos"])


async def _to_photo_response(photo: Photo) -> PhotoResponse:
    """
    Build a PhotoResponse with a real, loadable URL.

    Photo.url stores a bare object KEY (e.g. "users/<id>/<photo_id>.jpg"),
    not a usable URL — PhotoService.get_photo_url() resolves it based on
    moderation status: public URL if approved, short-lived signed URL
    otherwise (so owners can still see their own pending/rejected photos).
    """
    resolved_url = await PhotoService.get_photo_url(photo.url, photo.status)
    return PhotoResponse(
        id=photo.id,
        user_id=photo.user_id,
        url=resolved_url,
        order=photo.order,
        is_main=photo.is_main,
        status=photo.status,
        reject_reason=photo.reject_reason,
        face_verified=photo.face_verified,
        crop=photo.crop,  # Include crop data
    )


@router.post("", response_model=PhotoUploadResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PhotoUploadResponse:
    """
    Upload a profile photo.
    Photo is saved with status='pending' and must be approved by admin.
    New uploads are stored in the private bucket — not publicly visible
    until approved (see app/services/photo_service.py + admin_photos.py).
    """
    # Read file data
    file_data = await file.read()

    # Validate image
    is_valid, error = await PhotoService.validate_image(file_data, file.filename)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    # NSFW check
    is_safe, nsfw_score = await nsfw_service.check_image(file_data)
    if not is_safe:
        # Auto-reject and quarantine
        photo_id = str(uuid.uuid4())
        await nsfw_service.quarantine_photo(file_data, str(current_user.id), photo_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo rejected: content policy violation.",
        )

    # Face check: the uploaded photo must contain exactly one face. Same-person
    # identity is confirmed later by selfie verification (POST /users/me/verify),
    # which compares a fresh selfie against the profile photos — so no face
    # reference is stored or consulted at upload time.
    new_embedding, face_error = await face_verification_service.extract_single_photo_embedding(file_data)
    if face_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Photo rejected: {face_error}",
        )

    # Duplicate check: reject the same (or near-identical) image being uploaded
    # more than once. Compare only active photos (pending/approved) — a rejected
    # photo may be re-uploaded with fixes.
    new_phash = PhotoService.compute_phash(file_data)
    dup_result = await session.execute(
        select(Photo.phash).where(
            Photo.user_id == current_user.id,
            Photo.status.in_(["pending", "approved"]),
            Photo.phash.isnot(None),
        )
    )
    existing_hashes = [h for (h,) in dup_result.all()]
    if PhotoService.is_duplicate(existing_hashes, new_phash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo rejected: this photo is already uploaded.",
        )

    # Check photo limit atomically — FOR UPDATE locks existing rows so
    # a concurrent upload cannot read the same count and both pass the check.
    result = await session.execute(
        select(Photo)
        .where(Photo.user_id == current_user.id)
        .with_for_update()
    )
    photos = result.scalars().all()
    if len(photos) >= settings.MAX_PHOTOS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.MAX_PHOTOS_PER_USER} photos per user",
        )

    # Create photo record
    new_photo = Photo(
        user_id=current_user.id,
        url="",  # Will update after save
        order=len(photos),
        is_main=len(photos) == 0,  # First photo becomes main
        status="pending",
        nsfw_score=nsfw_score,
        phash=new_phash,
    )
    session.add(new_photo)
    await session.flush()  # Get ID

    # Upload to object storage (private bucket) — returns the object KEY,
    # not a loadable URL. See PhotoService.save_photo().
    photo_key = await PhotoService.save_photo(
        str(current_user.id),
        str(new_photo.id),
        file_data,
    )

    # Store the key
    new_photo.url = photo_key
    await session.commit()
    await session.refresh(new_photo)

    await invalidate_user_cache(redis_client, current_user.id)

    # Resolve a real, loadable (signed) URL for the immediate upload response
    display_url = await PhotoService.get_photo_url(photo_key, new_photo.status)

    return PhotoUploadResponse(
        id=new_photo.id,
        url=display_url,
        status="pending",
        message="Photo uploaded. Under review by admin.",
    )


@router.get("", response_model=list[PhotoResponse])
@limiter.limit("30/minute")
async def get_my_photos(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[PhotoResponse]:
    """
    Get all photos for current user.
    Shows all photos (pending, approved, rejected) — pending/rejected
    photos resolve to short-lived signed URLs so the owner can still
    view them (e.g. to see why a photo was rejected); approved photos
    resolve to plain public URLs.
    """
    result = await session.execute(
        select(Photo)
        .where(Photo.user_id == current_user.id)
        .order_by(Photo.order)
    )
    photos = result.scalars().all()
    return [await _to_photo_response(p) for p in photos]


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def delete_photo(
    request: Request,
    photo_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Delete a photo.
    """
    # Find photo
    result = await session.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == current_user.id,
        )
    )
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Photo not found",
        )

    # Delete from object storage (checks both buckets, since the photo
    # could be in either depending on its moderation status)
    await PhotoService.delete_photo(str(current_user.id), str(photo_id))

    # Track whether we need to reassign main before deleting
    was_main = photo.is_main

    # Delete from database
    await session.delete(photo)

    # If deleted photo was main, set new main — same transaction, no double commit
    if was_main:
        result = await session.execute(
            select(Photo)
            .where(Photo.user_id == current_user.id)
            .order_by(Photo.order)
            .limit(1)
        )
        first_photo = result.scalar_one_or_none()
        if first_photo:
            first_photo.is_main = True

    await session.commit()

    await invalidate_user_cache(redis_client, current_user.id)


@router.put("/{photo_id}/main", response_model=PhotoResponse)
@limiter.limit("20/minute")
async def set_main_photo(
    request: Request,
    photo_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PhotoResponse:
    """
    Set a photo as main profile photo.
    """
    # Find the photo to set as main
    result = await session.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == current_user.id,
        )
    )
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Photo not found",
        )

    if photo.status in ("rejected", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only approved photos can be set as main",
        )

    # Remove main flag from all photos
    await session.execute(
        update(Photo)
        .where(Photo.user_id == current_user.id)
        .values(is_main=False)
    )

    # Set new main photo
    photo.is_main = True
    await session.commit()
    await session.refresh(photo)
    await invalidate_user_cache(redis_client, current_user.id)

    return await _to_photo_response(photo)


@router.patch("/order", response_model=list[PhotoResponse])
@limiter.limit("10/minute")
async def reorder_photos(
    request: Request,
    body: PhotoUpdateOrderRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Reorder photos.
    Accepts a mapping of photo_id -> new order for the current user.
    """
    photos = (
        await session.execute(
            select(Photo).where(
                Photo.user_id == current_user.id,
                Photo.id.in_(request.orders.keys()),
            )
        )
    ).scalars().all()

    if len(photos) != len(request.orders):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more photos not found",
        )

    for photo in photos:
        photo.order = request.orders[photo.id]

    await session.commit()
    await invalidate_user_cache(redis_client, current_user.id)

    all_photos = (
        await session.execute(
            select(Photo)
            .where(Photo.user_id == current_user.id)
            .order_by(Photo.order)
        )
    ).scalars().all()

    return [await _to_photo_response(p) for p in all_photos]


# =============================================================================
# NEW: Update Crop Data
# =============================================================================

@router.patch("/{photo_id}/crop", response_model=PhotoResponse)
@limiter.limit("10/minute")
async def update_photo_crop(
    request: Request,
    photo_id: UUID,
    crop_data: PhotoUpdateCropRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PhotoResponse:
    """
    Update crop data for a photo.
    Stores crop position and scale for avatar display.
    """
    # Find the photo
    result = await session.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == current_user.id,
        )
    )
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Photo not found",
        )

    # Update crop data
    photo.crop = crop_data.crop.model_dump()

    await session.commit()
    await session.refresh(photo)
    await invalidate_user_cache(redis_client, current_user.id)

    return await _to_photo_response(photo)