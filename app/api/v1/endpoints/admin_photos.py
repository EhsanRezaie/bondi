from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID

from app.db.session import get_session
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from app.core.redis import redis_client
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.photo_service import PhotoService
from app.schemas.admin import (
    AdminPendingPhotoResponse,
    AdminPhotoActionResponse,
    AdminPhotoRejectResponse,
    AdminPhotoStatsResponse,
    AdminPhotoDetailResponse,
    AdminPhotoVerifyResponse,
    AdminUserPhotoResponse,
)

from app.core.logging import get_logger

logger = get_logger("admin_photos")

router = APIRouter(prefix="/admin/photos", tags=["admin"])


# =====================================================================
# TODO(face-verification): Wire in the real face-match API here.
#
# Plan: a third-party API takes two images (e.g. new upload vs. the
# user's existing main/verified photo, or a live selfie) and returns
# whether they're the same person.
#
# Once that's ready:
#   1. Call it from upload_photo() in photos.py right after save_photo(),
#      or from a background task/Celery job so upload doesn't block on it.
#   2. Store the result on Photo.face_verified (already exists on the model).
#   3. Decide whether a failed match should auto-reject the photo
#      (set status="rejected") or just flag it for a human admin to review
#      via the existing /admin/photos/pending queue.
#   4. The manual admin_verify_face() endpoint below can stay as a manual
#      override even after automation lands (e.g. for borderline cases).
#
# Until that's wired in: ALL uploaded photos are treated as verified
# automatically (see _AUTO_FACE_VERIFY below) so the moderation queue
# isn't blocked on a feature that doesn't exist yet.
# =====================================================================
_AUTO_FACE_VERIFY = False


@router.get("/pending", response_model=list[AdminPendingPhotoResponse])
@limiter.limit("60/minute")
async def admin_get_pending_photos(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get all pending photos"""

    query = (
        select(Photo, User, UserProfile)
        .join(User, Photo.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(Photo.status == "pending")
        .order_by(Photo.created_at)
        .offset(offset)
        .limit(limit)
    )

    result = await session.execute(query)
    rows = result.all()

    photos = []
    for photo, user, profile in rows:
        photos.append({
            "id": photo.id,
            "user_id": user.id,
            "user_name": profile.name if profile else None,
            "user_email": user.email,
            "url": await PhotoService.get_photo_url(photo.url, photo.status),
            "is_main": photo.is_main,
            "status": photo.status,
            "face_verified": getattr(photo, "face_verified", False),
            "created_at": photo.created_at.isoformat() if photo.created_at else None,
        })

    return photos


@router.post("/{photo_id}/approve", response_model=AdminPhotoActionResponse)
@limiter.limit("30/minute")
async def admin_approve_photo(
    request: Request,
    photo_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Approve a pending photo"""

    result = await session.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if photo.status != "pending":
        raise HTTPException(status_code=400, detail=f"Photo already {photo.status}")

    # Move the object from the private bucket to the public bucket BEFORE
    # flipping the DB status, so we never have status="approved" pointing
    # at an object that isn't actually publicly readable yet.
    await PhotoService.publish_photo(str(photo.user_id), str(photo.id))

    photo.status = "approved"
    await session.commit()

    await redis_client.delete(f"presign:{photo.url}")

    return {"message": "Photo approved successfully", "photo_id": str(photo_id)}


@router.post("/{photo_id}/reject", response_model=AdminPhotoRejectResponse)
@limiter.limit("30/minute")
async def admin_reject_photo(
    request: Request,
    photo_id: UUID,
    reason: str,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Reject a pending photo with reason"""

    result = await session.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if photo.status != "pending":
        raise HTTPException(status_code=400, detail=f"Photo already {photo.status}")

    # No storage move needed — rejected photos stay in the private bucket
    # (they were never published), kept around so the user can see why
    # their own photo was rejected via GET /users/me/photos.
    photo.status = "rejected"
    photo.reject_reason = reason
    await session.commit()

    await redis_client.delete(f"presign:{photo.url}")

    return {"message": "Photo rejected successfully", "photo_id": str(photo_id), "reason": reason}


@router.get("/stats", response_model=AdminPhotoStatsResponse)
@limiter.limit("60/minute")
async def admin_photo_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get photo moderation statistics"""

    result = await session.execute(
        select(Photo.status, func.count(Photo.id))
        .group_by(Photo.status)
    )
    stats = {status: count for status, count in result.all()}

    return {
        "pending": stats.get("pending", 0),
        "approved": stats.get("approved", 0),
        "rejected": stats.get("rejected", 0),
        "total": sum(stats.values())
    }


@router.get("/{photo_id}", response_model=AdminPhotoDetailResponse)
@limiter.limit("60/minute")
async def admin_get_photo(
    request: Request,
    photo_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get photo details with user info"""

    result = await session.execute(
        select(Photo, User, UserProfile)
        .join(User, Photo.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(Photo.id == photo_id)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")

    photo, user, profile = row

    return {
        "id": photo.id,
        "user_id": user.id,
        "user_name": profile.name if profile else None,
        "user_email": user.email,
        "url": await PhotoService.get_photo_url(photo.url, photo.status),
        "is_main": photo.is_main,
        "status": photo.status,
        "reject_reason": photo.reject_reason,
        "face_verified": getattr(photo, 'face_verified', False),
        "order": photo.order,
        "created_at": photo.created_at.isoformat() if photo.created_at else None
    }


@router.post("/{photo_id}/verify-face", response_model=AdminPhotoVerifyResponse)
@limiter.limit("30/minute")
async def admin_verify_face(
    request: Request,
    photo_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """
    Admin: Manually mark photo as face-verified.

    Kept as a manual override for after automated face-matching (see TODO
    at the top of this file) is wired in — useful for borderline cases an
    admin wants to force-approve despite an automated mismatch.
    """

    result = await session.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()

    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    photo.face_verified = True
    await session.commit()

    return {"message": "Photo verified", "photo_id": str(photo_id), "face_verified": True}


@router.get("/users/{user_id}/photos", response_model=list[AdminUserPhotoResponse])
@limiter.limit("60/minute")
async def admin_get_user_photos(
    request: Request,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get all photos for a specific user"""

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    photos_result = await session.execute(
        select(Photo).where(Photo.user_id == user_id).order_by(Photo.order)
    )
    photos = photos_result.scalars().all()

    return [
        {
            "id": p.id,
            "url": await PhotoService.get_photo_url(p.url, p.status),
            "is_main": p.is_main,
            "status": p.status,
            "reject_reason": p.reject_reason,
            "face_verified": getattr(p, 'face_verified', False),
            "order": p.order,
            "created_at": p.created_at.isoformat() if p.created_at else None
        }
        for p in photos
    ]