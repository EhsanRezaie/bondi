"""Face Verification API endpoints (image-based selfie flow)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_current_user_db
from app.core.limiter import limiter
from app.core.logging import get_logger
import app.core.redis as redis_module
from app.core.cache import invalidate_auth_user, invalidate_user_cache
from app.db.session import get_session
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.verify import (
    VerifyResponse,
    VerificationStatusResponse,
)
from app.services.face_verification_service import face_verification_service
from app.services.nsfw_service import nsfw_service
from app.services.photo_service import PhotoService

logger = get_logger("verify")

router = APIRouter(prefix="/users/me/verify", tags=["verification"])

# Redis key prefixes
ATTEMPTS_PREFIX = "verify_attempts:"
COOLDOWN_PREFIX = "verify_cooldown:"


def _increment_attempts(user_id: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    attempts_key = f"{ATTEMPTS_PREFIX}{user_id}:{today}"
    pipe = redis_module.redis_client.pipeline()
    pipe.incr(attempts_key)
    pipe.expire(attempts_key, 86400)
    return pipe


@router.post("", response_model=VerifyResponse)
@limiter.limit("5/minute")
async def verify_selfie(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
) -> VerifyResponse:
    """
    Verify identity with a clear frontal selfie.

    The selfie image is checked (single face, frontal, eyes open, not too
    small), its face embedding is compared against every approved profile
    photo, and on success the account is marked verified. The selfie
    embedding is stored as the canonical face reference.
    """
    # A verified account is STILL re-checked against its current photos: the
    # old unconditional fast path let someone swap in another person's photo
    # and stay "verified" forever. Cooldown/daily-attempt limits only gate
    # fresh verification attempts, so an already-verified user can always
    # re-confirm (and a mismatch below revokes the stale badge).
    already_verified = bool(
        current_user.profile and current_user.profile.is_verified
    )

    if not already_verified:
        # Check cooldown
        cooldown_key = f"{COOLDOWN_PREFIX}{current_user.id}"
        cooldown_ttl = await redis_module.redis_client.ttl(cooldown_key)
        if cooldown_ttl > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {cooldown_ttl // 3600} hours before retrying verification",
            )

        # Check daily attempts
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        attempts_key = f"{ATTEMPTS_PREFIX}{current_user.id}:{today}"
        attempts = await redis_module.redis_client.get(attempts_key)
        if attempts and int(attempts) >= settings.FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY:
            raise HTTPException(
                status_code=429,
                detail=f"Maximum {settings.FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY} attempts per day",
            )

    # Read image
    image_bytes = await file.read()
    max_size = settings.FACE_VERIFICATION_MAX_SIZE_MB * 1024 * 1024
    if len(image_bytes) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Maximum {settings.FACE_VERIFICATION_MAX_SIZE_MB}MB",
        )

    # NSFW check on the selfie
    is_safe, _ = await nsfw_service.check_image(image_bytes)
    if not is_safe:
        raise HTTPException(status_code=400, detail="Selfie rejected: content policy violation.")

    # Extract selfie embedding (includes selfie image checks)
    selfie_embedding, error = await face_verification_service.extract_image_embedding(image_bytes)
    if error:
        await _increment_attempts(str(current_user.id)).execute()
        raise HTTPException(status_code=400, detail=error)

    # Load the user's photos (pending or approved — NOT rejected). During
    # onboarding photos are still pending, so selfie verification must be able
    # to compare against them before they are approved.
    result = await session.execute(
        select(Photo).where(
            Photo.user_id == current_user.id,
            Photo.status.in_(["pending", "approved"]),
        )
    )
    photos = list(result.scalars().all())

    if len(photos) < settings.FACE_VERIFICATION_MIN_PHOTOS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload at least {settings.FACE_VERIFICATION_MIN_PHOTOS} photo(s) first",
        )

    # Download photos and compare the selfie against EACH one. Collect every
    # photo that doesn't match so the app can tell the user which ones failed.
    mismatched_photo_ids: list[str] = []
    matched_any = False
    best_similarity = 0.0
    for photo in photos:
        try:
            photo_bytes = await PhotoService.download_photo_bytes(photo.url)
        except Exception as e:
            logger.warning("photo_download_failed", photo_id=str(photo.id), error=str(e))
            continue

        photo_embedding, _ = await face_verification_service.extract_single_photo_embedding(photo_bytes)
        if photo_embedding is None:
            continue

        matched, similarity = face_verification_service.compare_embeddings(
            selfie_embedding, photo_embedding
        )
        best_similarity = max(best_similarity, similarity)
        if matched:
            matched_any = True
        else:
            mismatched_photo_ids.append(str(photo.id))

    if not matched_any or mismatched_photo_ids:
        if not already_verified:
            await _increment_attempts(str(current_user.id)).execute()
        # A previously verified account whose photos no longer match the
        # selfie has been tampered with — revoke the badge so the mismatch is
        # actually reflected instead of silently kept.
        if already_verified and current_user.profile is not None:
            current_user.profile.is_verified = False
            current_user.profile.verified_at = None
            await session.commit()
            await invalidate_auth_user(redis_module.redis_client, current_user.id)
            await invalidate_user_cache(redis_module.redis_client, current_user.id)
        logger.warning(
            "face_match_failed",
            user_id=str(current_user.id),
            mismatched_photo_ids=mismatched_photo_ids,
            best_similarity=round(best_similarity, 4),
            threshold=settings.FACE_MATCH_THRESHOLD,
        )
        return VerifyResponse(
            verified=False,
            message=(
                "Some of your photos didn't match your selfie. Update them and "
                "try again, or send a ticket for manual review."
                if mismatched_photo_ids
                else "Could not match your selfie with your photos. Try again in good lighting."
            ),
            similarity_score=best_similarity if settings.DEBUG else None,
            mismatched_photo_ids=mismatched_photo_ids,
        )

    # Success — mark user as verified, face-verify every photo, and auto-approve
    # (publish) any that were still pending so the profile becomes visible.
    now = datetime.now(timezone.utc)
    current_user.profile.is_verified = True
    current_user.profile.verified_at = now

    for photo in photos:
        photo.face_verified = True
        if photo.status != "approved":
            photo.status = "approved"
            await PhotoService.publish_photo(str(current_user.id), str(photo.id))

    await session.commit()

    # Store the selfie embedding as the canonical face reference
    await redis_module.store_face_reference(
        str(current_user.id),
        selfie_embedding.tolist(),
        ttl=settings.FACE_REFERENCE_CACHE_TTL,
    )

    # Invalidate caches — verification status changed
    await invalidate_auth_user(redis_module.redis_client, current_user.id)
    await invalidate_user_cache(redis_module.redis_client, current_user.id)

    # Cooldown + attempt counter only apply to fresh verification attempts.
    if not already_verified:
        cooldown_key = f"{COOLDOWN_PREFIX}{current_user.id}"
        await redis_module.redis_client.set(cooldown_key, "1", ex=settings.FACE_VERIFICATION_COOLDOWN_TTL)
        await _increment_attempts(str(current_user.id)).execute()

    logger.info(
        "verification_success",
        user_id=str(current_user.id),
        similarity_score=round(best_similarity, 4),
    )

    return VerifyResponse(
        verified=True,
        message="Profile verified successfully!",
        similarity_score=best_similarity if settings.DEBUG else None,
        mismatched_photo_ids=[],
    )


@router.get("/status", response_model=VerificationStatusResponse)
@limiter.limit("30/minute")
async def get_verification_status(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> VerificationStatusResponse:
    """Check verification status and eligibility."""
    is_verified = current_user.profile.is_verified if current_user.profile else False
    verified_at = current_user.profile.verified_at if current_user.profile else None

    # Check cooldown
    cooldown_key = f"{COOLDOWN_PREFIX}{current_user.id}"
    cooldown_ttl = await redis_module.redis_client.ttl(cooldown_key)
    eligible_to_verify = not is_verified and cooldown_ttl <= 0

    cooldown_remaining = cooldown_ttl if cooldown_ttl > 0 else None

    return VerificationStatusResponse(
        is_verified=is_verified,
        verified_at=verified_at,
        eligible_to_verify=eligible_to_verify,
        cooldown_remaining_seconds=cooldown_remaining,
    )
