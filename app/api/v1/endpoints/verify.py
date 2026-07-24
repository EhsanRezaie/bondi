"""Face Verification API endpoints."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.logging import get_logger
import app.core.redis as redis_module
from app.db.session import get_session
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.verify import (
    ChallengeResponse,
    VerifyRequest,
    VerifyResponse,
    VerificationStatusResponse,
)
from app.services.face_verification_service import (
    CHALLENGE_TYPES,
    face_verification_service,
)
from app.services.photo_service import PhotoService

logger = get_logger("verify")

router = APIRouter(prefix="/users/me/verify", tags=["verification"])

# Redis key prefixes
CHALLENGE_PREFIX = "verify_challenge:"
ATTEMPTS_PREFIX = "verify_attempts:"
COOLDOWN_PREFIX = "verify_cooldown:"


@router.post("/challenge", response_model=ChallengeResponse)
@limiter.limit("5/minute")
async def generate_challenge(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ChallengeResponse:
    """
    Generate a random liveness challenge for face verification.

    Returns a challenge type and instructions for the user to perform
    while recording a selfie video.
    """
    # Check if already verified
    if current_user.profile and current_user.profile.is_verified:
        raise HTTPException(status_code=400, detail="Profile already verified")

    # Check cooldown
    cooldown_key = f"{COOLDOWN_PREFIX}{current_user.id}"
    cooldown_exists = await redis_module.redis_client.exists(cooldown_key)
    if cooldown_exists:
        ttl = await redis_module.redis_client.ttl(cooldown_key)
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {ttl // 3600} hours before retrying verification",
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

    # Generate random challenge
    import random
    challenge_type = random.choice(list(CHALLENGE_TYPES.keys()))
    challenge_id = str(uuid.uuid4())

    # Store challenge in Redis
    challenge_data = {
        "challenge_id": challenge_id,
        "challenge_type": challenge_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    challenge_key = f"{CHALLENGE_PREFIX}{current_user.id}"
    await redis_module.redis_client.set(
        challenge_key,
        json.dumps(challenge_data),
        ex=settings.FACE_VERIFICATION_CHALLENGE_TTL,
    )

    logger.info(
        "challenge_generated",
        user_id=str(current_user.id),
        challenge_type=challenge_type,
    )

    return ChallengeResponse(
        challenge_type=challenge_type,
        instructions=CHALLENGE_TYPES[challenge_type],
        challenge_id=challenge_id,
        expires_in_seconds=settings.FACE_VERIFICATION_CHALLENGE_TTL,
    )


@router.post("", response_model=VerifyResponse)
@limiter.limit("3/minute")
async def verify_video(
    request: Request,
    file: UploadFile = File(...),
    challenge_id: str = "",
    challenge_type: str = "",
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> VerifyResponse:
    """
    Submit a selfie video for face verification.

    The video must contain the user performing the requested liveness
    challenge. The backend will:
    1. Validate the challenge was performed
    2. Extract face embeddings from the video
    3. Compare against the user's existing profile photos
    4. Mark as verified if similarity exceeds threshold
    """
    # Check if already verified
    if current_user.profile and current_user.profile.is_verified:
        raise HTTPException(status_code=400, detail="Profile already verified")

    # Validate challenge parameters
    if not challenge_id or not challenge_type:
        raise HTTPException(status_code=400, detail="challenge_id and challenge_type are required")

    # Retrieve challenge from Redis
    challenge_key = f"{CHALLENGE_PREFIX}{current_user.id}"
    challenge_data = await redis_module.redis_client.get(challenge_key)
    if not challenge_data:
        raise HTTPException(status_code=400, detail="Challenge expired. Please request a new one.")

    challenge = json.loads(challenge_data)

    # Validate challenge
    if challenge["challenge_id"] != challenge_id:
        raise HTTPException(status_code=400, detail="Invalid challenge. Please request a new one.")
    if challenge["challenge_type"] != challenge_type:
        raise HTTPException(status_code=400, detail="Challenge type mismatch. Please request a new one.")
    if challenge["status"] != "pending":
        raise HTTPException(status_code=400, detail="Challenge already used. Please request a new one.")

    # Read video
    video_bytes = await file.read()
    max_size = settings.FACE_VERIFICATION_MAX_SIZE_MB * 1024 * 1024
    if len(video_bytes) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"Video too large. Maximum {settings.FACE_VERIFICATION_MAX_SIZE_MB}MB",
        )

    # Process video (decode and extract frames)
    frames, error = await face_verification_service.process_video(video_bytes)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Validate liveness challenge
    challenge_passed, challenge_msg = await face_verification_service.validate_challenge(
        frames, challenge_type
    )
    if not challenge_passed:
        # Increment attempt counter
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        attempts_key = f"{ATTEMPTS_PREFIX}{current_user.id}:{today}"
        pipe = redis_module.redis_client.pipeline()
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, 86400)
        await pipe.execute()

        logger.warning(
            "liveness_challenge_failed",
            user_id=str(current_user.id),
            challenge_type=challenge_type,
            reason=challenge_msg,
        )
        raise HTTPException(status_code=400, detail=f"Liveness check failed: {challenge_msg}")

    # Extract video embeddings
    video_embedding, error = await face_verification_service.extract_video_embeddings(frames)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Load user's approved photos
    result = await session.execute(
        select(Photo).where(
            Photo.user_id == current_user.id,
            Photo.status == "approved",
        )
    )
    photos = result.scalars().all()

    if len(photos) < settings.FACE_VERIFICATION_MIN_PHOTOS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload at least {settings.FACE_VERIFICATION_MIN_PHOTOS} approved photo(s) first",
        )

    # Download and extract embeddings from photos
    photo_bytes_list = []
    for photo in photos:
        try:
            photo_bytes = await PhotoService.download_photo_bytes(photo.url)
            photo_bytes_list.append(photo_bytes)
        except Exception as e:
            logger.warning(
                "photo_download_failed",
                photo_id=str(photo.id),
                error=str(e),
            )
            continue

    if not photo_bytes_list:
        raise HTTPException(status_code=400, detail="Could not load profile photos")

    photo_embedding, error = await face_verification_service.extract_photo_embeddings(photo_bytes_list)
    if error:
        raise HTTPException(status_code=400, detail=error)

    # Compare embeddings
    matched, similarity_score = face_verification_service.compare_embeddings(
        video_embedding, photo_embedding
    )

    if not matched:
        # Increment attempt counter
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        attempts_key = f"{ATTEMPTS_PREFIX}{current_user.id}:{today}"
        pipe = redis_module.redis_client.pipeline()
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, 86400)
        await pipe.execute()

        logger.warning(
            "face_match_failed",
            user_id=str(current_user.id),
            similarity_score=similarity_score,
            threshold=settings.FACE_MATCH_THRESHOLD,
        )
        raise HTTPException(
            status_code=400,
            detail="Verification failed. Ensure good lighting and that you match your photos.",
        )

    # Success - mark user as verified
    now = datetime.now(timezone.utc)
    current_user.profile.is_verified = True
    current_user.profile.verified_at = now

    # Mark all approved photos as face_verified
    for photo in photos:
        photo.face_verified = True

    await session.commit()

    # Update challenge status
    challenge["status"] = "completed"
    await redis_module.redis_client.set(
        challenge_key,
        json.dumps(challenge),
        ex=settings.FACE_VERIFICATION_CHALLENGE_TTL,
    )

    # Set cooldown
    cooldown_key = f"{COOLDOWN_PREFIX}{current_user.id}"
    await redis_module.redis_client.set(cooldown_key, "1", ex=settings.FACE_VERIFICATION_COOLDOWN_TTL)

    # Increment attempt counter
    today = now.strftime("%Y-%m-%d")
    attempts_key = f"{ATTEMPTS_PREFIX}{current_user.id}:{today}"
    pipe = redis_module.redis_client.pipeline()
    pipe.incr(attempts_key)
    pipe.expire(attempts_key, 86400)
    await pipe.execute()

    logger.info(
        "verification_success",
        user_id=str(current_user.id),
        similarity_score=similarity_score,
    )

    return VerifyResponse(
        verified=True,
        message="Profile verified successfully!",
        similarity_score=similarity_score if settings.DEBUG else None,
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
