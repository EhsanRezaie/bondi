import io
import uuid
from typing import Optional, Tuple

from PIL import Image
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger
import app.core.redis as redis_module
from app.core.redis import redis_client
from app.services.storage import s3_client as _s3_client

logger = get_logger("photo_service")


class PhotoService:
    """Handle photo upload, validation, and storage (MinIO / S3-compatible)."""

    MAX_FILE_SIZE = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    ALLOWED_FORMATS = ["JPEG", "PNG", "WEBP"]
    MIN_WIDTH = 200
    MIN_HEIGHT = 200
    MAX_WIDTH = 5000
    MAX_HEIGHT = 5000

    # Statuses considered "public" — object lives in the public bucket, served via direct URL.
    # Anything else (pending, rejected) lives in the private bucket, served via short-lived signed URL.
    PUBLIC_STATUSES = {"approved"}

    @staticmethod
    async def validate_image(file_data: bytes, filename: str) -> Tuple[bool, Optional[str]]:
        """
        Validate image file.
        Returns: (is_valid, error_message)
        """
        # Check file size
        if len(file_data) > PhotoService.MAX_FILE_SIZE:
            return False, f"Image too large. Max {PhotoService.MAX_FILE_SIZE // (1024*1024)}MB"

        try:
            # Open and validate image
            image = Image.open(io.BytesIO(file_data))

            # Check format
            if image.format not in PhotoService.ALLOWED_FORMATS:
                return False, f"Invalid format. Allowed: {', '.join(PhotoService.ALLOWED_FORMATS)}"

            # Check dimensions
            width, height = image.size
            if width < PhotoService.MIN_WIDTH or height < PhotoService.MIN_HEIGHT:
                return False, f"Image too small. Minimum {PhotoService.MIN_WIDTH}x{PhotoService.MIN_HEIGHT}"

            if width > PhotoService.MAX_WIDTH or height > PhotoService.MAX_HEIGHT:
                return False, f"Image too large. Maximum {PhotoService.MAX_WIDTH}x{PhotoService.MAX_HEIGHT}"

            # Check aspect ratio (not too extreme)
            ratio = width / height
            if ratio > 3 or ratio < 0.33:
                return False, "Image aspect ratio too extreme. Use normal photos."

            return True, None

        except Exception as e:
            logger.error("Image validation error", error=str(e))
            return False, "Invalid or corrupted image file"

    @staticmethod
    def _object_key(user_id: str, photo_id: str) -> str:
        """Object key is identical in both buckets — only the bucket (and therefore
        the access policy) differs depending on moderation status."""
        return f"users/{user_id}/{photo_id}.jpg"

    @staticmethod
    def _optimize_image(file_data: bytes) -> bytes:
        """Resize/convert/compress, returning ready-to-upload JPEG bytes.
        EXIF metadata is stripped to prevent GPS/device info leakage."""
        image = Image.open(io.BytesIO(file_data))

        # Convert to RGB if needed (for PNG with transparency)
        if image.mode in ("RGBA", "LA", "P"):
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            image = rgb_image
        elif image.mode != "RGB":
            image = image.convert("RGB")

        # Resize if too large (max 1200px)
        max_size = 1200
        if image.width > max_size or image.height > max_size:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Strip EXIF by creating a fresh image from pixel data
        clean = Image.new(image.mode, image.size)
        clean.putdata(list(image.get_flattened_data()))

        buffer = io.BytesIO()
        clean.save(buffer, "JPEG", quality=85, optimize=True)
        return buffer.getvalue()

    @staticmethod
    async def save_photo(user_id: str, photo_id: str, file_data: bytes) -> str:
        """
        Optimize and upload a newly-submitted photo to the PRIVATE bucket
        (new uploads always start as 'pending', so they're not publicly visible
        until an admin/automated check approves them).

        Returns the object KEY (not a URL) — store this in Photo.url.
        Resolve it to an actual loadable URL via get_photo_url() at read time.
        """
        optimized = PhotoService._optimize_image(file_data)
        key = PhotoService._object_key(user_id, photo_id)

        async with _s3_client() as s3:
            await s3.put_object(
                Bucket=settings.S3_PRIVATE_BUCKET,
                Key=key,
                Body=optimized,
                ContentType="image/jpeg",
            )

            logger.info("Uploaded photo to private bucket", key=key)
        return key

    @staticmethod
    async def delete_photo(user_id: str, photo_id: str) -> bool:
        """Delete photo from whichever bucket it currently lives in."""
        key = PhotoService._object_key(user_id, photo_id)
        deleted = False

        async with _s3_client() as s3:
            for bucket in (settings.S3_PRIVATE_BUCKET, settings.S3_PUBLIC_BUCKET):
                try:
                    await s3.head_object(Bucket=bucket, Key=key)
                except ClientError as e:
                    # Object already gone (or never made it to this bucket) —
                    # normal for deletes; not an error. Other failures (auth,
                    # network, server errors) still get logged loudly.
                    code = e.response.get("Error", {}).get("Code", "")
                    if code in ("404", "NoSuchKey", "NotFound"):
                        continue
                    logger.warning("photo_delete_head_failed", key=key, bucket=bucket, error=str(e), exc_info=True)
                    continue  # not in this bucket
                await s3.delete_object(Bucket=bucket, Key=key)
                deleted = True
                logger.info("Deleted photo", key=key, bucket=bucket)

        return deleted

    @staticmethod
    async def publish_photo(user_id: str, photo_id: str) -> None:
        """
        Move a photo from the PRIVATE bucket to the PUBLIC bucket.
        Call this when admin/automated moderation sets status='approved'.

        NOTE: object ACL must be set explicitly to public-read on copy —
        being in a bucket *named* "public" does not make an object public.
        For this to actually serve anonymous reads, the S3_PUBLIC_BUCKET
        also needs a bucket policy allowing public GetObject (see the
        docker-compose / MinIO setup notes — `mc anonymous set download`).
        """
        key = PhotoService._object_key(user_id, photo_id)
        copy_source = {"Bucket": settings.S3_PRIVATE_BUCKET, "Key": key}

        async with _s3_client() as s3:
            await s3.copy_object(
                Bucket=settings.S3_PUBLIC_BUCKET,
                Key=key,
                CopySource=copy_source,
                ACL="public-read",
            )
            await s3.delete_object(Bucket=settings.S3_PRIVATE_BUCKET, Key=key)

        logger.info("Published photo to public bucket", key=key)

    @staticmethod
    async def unpublish_photo(user_id: str, photo_id: str) -> None:
        """
        Move a photo from the PUBLIC bucket back to PRIVATE.
        Call this if an approved photo is later rejected/removed (e.g. a report
        leads to it being taken down again).
        """
        key = PhotoService._object_key(user_id, photo_id)
        copy_source = {"Bucket": settings.S3_PUBLIC_BUCKET, "Key": key}

        async with _s3_client() as s3:
            await s3.copy_object(
                Bucket=settings.S3_PRIVATE_BUCKET,
                Key=key,
                CopySource=copy_source,
            )
            await s3.delete_object(Bucket=settings.S3_PUBLIC_BUCKET, Key=key)

        logger.info("Unpublished photo back to private bucket", key=key)

    @staticmethod
    async def get_photo_url(key: str, status: str) -> str:
        """
        Resolve a stored object key into an actual loadable URL, based on
        moderation status:
          - approved  -> public bucket, plain fast URL (no signing cost)
          - otherwise -> private bucket, short-lived signed URL (cached in
                         Redis for 5 min to avoid repeated MinIO round-trips)
        """
        if status in PhotoService.PUBLIC_STATUSES:
            return f"{settings.S3_PUBLIC_BASE_URL}/{key}"

        cache_key = f"presign:{key}"
        cached = await redis_client.get(cache_key)
        if cached:
            return cached

        async with _s3_client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_PRIVATE_BUCKET, "Key": key},
                ExpiresIn=settings.S3_SIGNED_URL_EXPIRE_SECONDS,
            )

        await redis_client.setex(cache_key, 300, url)
        return url
    @staticmethod
    async def download_photo_bytes(key: str) -> bytes:
        """Download raw photo bytes from MinIO (public or private bucket)."""
        async with _s3_client() as s3:
            # Try public bucket first (approved photos)
            try:
                response = await s3.get_object(
                    Bucket=settings.S3_PUBLIC_BUCKET, Key=key
                )
                return await response["Body"].read()
            except ClientError:
                pass
            # Fall back to private bucket (pending photos)
            try:
                response = await s3.get_object(
                    Bucket=settings.S3_PRIVATE_BUCKET, Key=key
                )
                return await response["Body"].read()
            except ClientError as e:
                logger.error("download_photo_failed", key=key, error=str(e))
                raise
