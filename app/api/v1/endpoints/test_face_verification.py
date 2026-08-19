"""Test endpoint for face verification — admin-only debug tool (image-based)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from app.core.logging import get_logger
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
    Admin-only test endpoint for the image-based face verification pipeline.

    Upload a selfie image and specify a user_id to test against.
    Returns detailed results at each pipeline step without modifying
    any database records.
    """
    results = {"steps": [], "success": False}

    # Step 1: Validate user exists
    results["steps"].append({"step": "validate_user", "status": "started"})
    try:
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
    results["steps"][-1].update({"status": "passed", "user_id": str(user.id)})

    # Step 2: Read image
    results["steps"].append({"step": "read_image", "status": "started"})
    image_bytes = await file.read()
    max_size = settings.FACE_VERIFICATION_MAX_SIZE_MB * 1024 * 1024
    if len(image_bytes) > max_size:
        results["steps"][-1].update({"status": "failed", "error": f"Image too large: {len(image_bytes)} bytes"})
        return results
    results["steps"][-1].update({"status": "passed", "size_bytes": len(image_bytes)})

    # Step 3: Selfie checks + extract embedding
    results["steps"].append({"step": "extract_image_embedding", "status": "started"})
    selfie_embedding, error = await face_verification_service.extract_image_embedding(image_bytes)
    if error:
        results["steps"][-1].update({"status": "failed", "error": error})
        return results
    results["steps"][-1].update({
        "status": "passed",
        "embedding_dim": int(selfie_embedding.shape[0]),
        "norm": float(selfie_embedding.sum()),
    })

    # Step 4: Load user's approved photos
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

    # Step 5: Compare selfie embedding against each photo
    results["steps"].append({"step": "compare_embeddings", "status": "started"})
    per_photo = []
    matched_any = False
    best_similarity = 0.0
    for photo in photos:
        try:
            photo_bytes = await PhotoService.download_photo_bytes(photo.url)
        except Exception as e:
            logger.warning("face_verification_photo_download_failed", photo_id=str(photo.id), error=str(e), exc_info=True)
            continue

        photo_embedding, _ = await face_verification_service.extract_single_photo_embedding(photo_bytes)
        if photo_embedding is None:
            per_photo.append({"photo_id": str(photo.id), "status": "no_face"})
            continue

        matched, similarity = face_verification_service.compare_embeddings(selfie_embedding, photo_embedding)
        best_similarity = max(best_similarity, similarity)
        if matched:
            matched_any = True
        per_photo.append({
            "photo_id": str(photo.id),
            "matched": matched,
            "similarity_score": round(similarity, 4),
        })

    results["steps"][-1].update({
        "status": "passed",
        "per_photo": per_photo,
        "best_similarity_score": round(best_similarity, 4),
        "threshold": settings.FACE_MATCH_THRESHOLD,
    })

    results["success"] = matched_any
    results["summary"] = {
        "similarity_score": round(best_similarity, 4),
        "threshold": settings.FACE_MATCH_THRESHOLD,
        "face_match": matched_any,
        "would_verify": matched_any,
    }

    logger.info(
        "face_verification_test",
        user_id=user_id,
        similarity_score=round(best_similarity, 4),
        matched=matched_any,
    )

    return results
