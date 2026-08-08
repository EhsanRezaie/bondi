"""Test endpoint for face verification — admin-only debug tool."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.db.session import get_session
from app.models.photo import Photo
from app.models.user import User
from app.services.face_verification_service import face_verification_service
from app.services.photo_service import PhotoService

logger = get_logger("test_face_verification")

router = APIRouter(prefix="/admin/face-verification", tags=["admin"])


@router.post("/test")
@limiter.limit("10/minute")
async def test_face_verification(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = "",
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """
    Admin-only test endpoint for face verification pipeline.

    Upload a selfie video and specify a user_id to test against.
    Returns detailed results at each pipeline step without modifying
    any database records.
    """
    results = {"steps": [], "success": False}

    # Step 1: Validate user exists
    results["steps"].append({"step": "validate_user", "status": "started"})
    try:
        from uuid import UUID
        user_uuid = UUID(user_id)
    except ValueError as e:
        logger.warning("face_verification_invalid_user_id", user_id=user_id, error=str(e), exc_info=True)
        results["steps"][-1].update({"status": "failed", "error": "Invalid user_id format"})
        return results

    user_result = await session.execute(select(User).where(User.id == user_uuid))
    user = user_result.scalar_one_or_none()
    if not user:
        results["steps"][-1].update({"status": "failed", "error": "User not found"})
        return results
    results["steps"][-1].update({"status": "passed", "user_email": user.email})

    # Step 2: Read video
    results["steps"].append({"step": "read_video", "status": "started"})
    video_bytes = await file.read()
    max_size = settings.FACE_VERIFICATION_MAX_SIZE_MB * 1024 * 1024
    if len(video_bytes) > max_size:
        results["steps"][-1].update({"status": "failed", "error": f"Video too large: {len(video_bytes)} bytes"})
        return results
    results["steps"][-1].update({"status": "passed", "size_bytes": len(video_bytes)})

    # Step 3: Process video (decode + extract frames)
    results["steps"].append({"step": "process_video", "status": "started"})
    frames, error = await face_verification_service.process_video(video_bytes)
    if error:
        results["steps"][-1].update({"status": "failed", "error": error})
        return results
    results["steps"][-1].update({"status": "passed", "frames_extracted": len(frames)})

    # Step 4: Face detection per frame
    results["steps"].append({"step": "face_detection", "status": "started"})
    faces_per_frame = []
    for i, frame in enumerate(frames):
        face = face_verification_service._get_face(frame)
        faces_per_frame.append({
            "frame": i,
            "detected": face is not None,
            "has_embedding": face is not None and hasattr(face, 'normed_embedding'),
            "has_landmarks": face is not None and hasattr(face, 'landmark_3d_68'),
        })

    detected_count = sum(1 for f in faces_per_frame if f["detected"])
    if detected_count == 0:
        results["steps"][-1].update({"status": "failed", "error": "No faces detected in any frame", "details": faces_per_frame})
        return results
    results["steps"][-1].update({"status": "passed", "faces_detected": detected_count, "total_frames": len(frames)})

    # Step 5: Extract video embeddings
    results["steps"].append({"step": "extract_video_embeddings", "status": "started"})
    video_embedding, error = await face_verification_service.extract_video_embeddings(frames)
    if error:
        results["steps"][-1].update({"status": "failed", "error": error})
        return results
    results["steps"][-1].update({
        "status": "passed",
        "embedding_dim": int(video_embedding.shape[0]) if hasattr(video_embedding, 'shape') else None,
        "norm": float(video_embedding.sum()),
    })

    # Step 6: Load user's approved photos
    results["steps"].append({"step": "load_photos", "status": "started"})
    photos_result = await session.execute(
        select(Photo).where(
            Photo.user_id == user_uuid,
            Photo.status == "approved",
        )
    )
    photos = photos_result.scalars().all()
    if not photos:
        results["steps"][-1].update({"status": "failed", "error": "No approved photos found for user"})
        return results
    results["steps"][-1].update({"status": "passed", "photo_count": len(photos)})

    # Step 7: Download and extract photo embeddings
    results["steps"].append({"step": "extract_photo_embeddings", "status": "started"})
    photo_bytes_list = []
    download_errors = []
    for photo in photos:
        try:
            photo_bytes = await PhotoService.download_photo_bytes(photo.url)
            photo_bytes_list.append(photo_bytes)
        except Exception as e:
            logger.warning("face_verification_photo_download_failed", photo_id=str(photo.id), error=str(e), exc_info=True)
            download_errors.append({"photo_id": str(photo.id), "error": str(e)})

    if not photo_bytes_list:
        results["steps"][-1].update({"status": "failed", "error": "Could not download any photos", "details": download_errors})
        return results

    photo_embedding, error = await face_verification_service.extract_photo_embeddings(photo_bytes_list)
    if error:
        results["steps"][-1].update({"status": "failed", "error": error, "download_errors": download_errors})
        return results
    results["steps"][-1].update({
        "status": "passed",
        "photos_processed": len(photo_bytes_list),
        "download_errors": download_errors if download_errors else None,
    })

    # Step 8: Compare embeddings
    results["steps"].append({"step": "compare_embeddings", "status": "started"})
    matched, similarity_score = face_verification_service.compare_embeddings(video_embedding, photo_embedding)
    results["steps"][-1].update({
        "status": "passed",
        "similarity_score": similarity_score,
        "threshold": settings.FACE_MATCH_THRESHOLD,
        "matched": matched,
    })

    # Step 9: Liveness test (blink challenge as default)
    results["steps"].append({"step": "liveness_test", "status": "started"})
    challenge_passed, challenge_msg = await face_verification_service.validate_challenge(frames, "blink")
    results["steps"][-1].update({
        "status": "passed" if challenge_passed else "info",
        "challenge_type": "blink",
        "passed": challenge_passed,
        "message": challenge_msg,
    })

    results["success"] = matched and challenge_passed
    results["summary"] = {
        "similarity_score": similarity_score,
        "threshold": settings.FACE_MATCH_THRESHOLD,
        "face_match": matched,
        "liveness_passed": challenge_passed,
        "would_verify": matched and challenge_passed,
    }

    logger.info(
        "face_verification_test",
        user_id=user_id,
        similarity_score=similarity_score,
        matched=matched,
        liveness=challenge_passed,
    )

    return results
