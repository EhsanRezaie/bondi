import io
import uuid
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger
import app.core.redis as redis_module
from app.core.redis import redis_client
from app.services.storage import s3_client as _s3_client

logger = get_logger("photo_service")


class PhotoService:
    """Handle photo upload, validation, and storage (MinIO / S3-compatible).

    Photos are stored as WebP (smaller than JPEG at equal quality) with a small
    thumbnail alongside each one for list/card views.
    """

    MAX_FILE_SIZE = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    ALLOWED_FORMATS = ["JPEG", "PNG", "WEBP"]
    MIN_WIDTH = 200
    MIN_HEIGHT = 200
    MAX_WIDTH = 5000
    MAX_HEIGHT = 5000

    # Encoding targets — keep storage small without visible quality loss.
    FULL_MAX = 1080
    THUMB_MAX = 320
    IMAGE_QUALITY = 78
    THUMB_QUALITY = 75

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
        """Current object key (WebP). Object key is identical in both buckets —
        only the bucket (and therefore the access policy) differs depending on
        moderation status."""
        return f"users/{user_id}/{photo_id}.webp"

    @staticmethod
    def _legacy_object_key(user_id: str, photo_id: str) -> str:
        """Pre-WebP key still used by already-stored photos."""
        return f"users/{user_id}/{photo_id}.jpg"

    @staticmethod
    def _candidate_keys(user_id: str, photo_id: str) -> List[str]:
        return [
            PhotoService._object_key(user_id, photo_id),
            PhotoService._legacy_object_key(user_id, photo_id),
        ]

    @staticmethod
    def thumb_key(key: str) -> str:
        """Derive the thumbnail object key (inserts `_thumb`)."""
        directory, _, filename = key.rpartition("/")
        if "." in filename:
            base, ext = filename.rsplit(".", 1)
            thumb = f"{base}_thumb.{ext}"
        else:
            thumb = f"{filename}_thumb"
        return f"{directory}/{thumb}" if directory else thumb

    @staticmethod
    def _to_rgb(image: Image.Image) -> Image.Image:
        if image.mode in ("RGBA", "LA", "P"):
            rgb = Image.new("RGB", image.size, (255, 255, 255))
            rgb.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            return rgb
        if image.mode != "RGB":
            return image.convert("RGB")
        return image

    @staticmethod
    def _encode_webp(image: Image.Image, max_size: int, quality: int) -> bytes:
        """Resize to fit `max_size` and encode to lossy WebP. EXIF is stripped
        (not passed to the encoder), preventing GPS/device leakage."""
        img = image.copy()
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, "WEBP", quality=quality, method=4)
        return buffer.getvalue()

    @staticmethod
    def _optimize_image(file_data: bytes) -> bytes:
        """Return ready-to-upload full-size WebP bytes."""
        image = PhotoService._to_rgb(Image.open(io.BytesIO(file_data)))
        return PhotoService._encode_webp(image, PhotoService.FULL_MAX, PhotoService.IMAGE_QUALITY)

    @staticmethod
    async def save_photo(user_id: str, photo_id: str, file_data: bytes) -> str:
        """
        Optimize and upload a newly-submitted photo (full + thumbnail) to the
        PRIVATE bucket (new uploads always start as 'pending', so they're not
        publicly visible until an admin/automated check approves them).

        Returns the object KEY (not a URL) — store this in Photo.url.
        Resolve it to an actual loadable URL via get_photo_url() at read time.
        """
        image = PhotoService._to_rgb(Image.open(io.BytesIO(file_data)))
        full = PhotoService._encode_webp(image, PhotoService.FULL_MAX, PhotoService.IMAGE_QUALITY)
        thumb = PhotoService._encode_webp(image, PhotoService.THUMB_MAX, PhotoService.THUMB_QUALITY)

        key = PhotoService._object_key(user_id, photo_id)
        thumb_key = PhotoService.thumb_key(key)

        async with _s3_client() as s3:
            await s3.put_object(
                Bucket=settings.S3_PRIVATE_BUCKET,
                Key=key,
                Body=full,
                ContentType="image/webp",
            )
            await s3.put_object(
                Bucket=settings.S3_PRIVATE_BUCKET,
                Key=thumb_key,
                Body=thumb,
                ContentType="image/webp",
            )

            logger.info("Uploaded photo to private bucket", key=key)
        return key

    @staticmethod
    async def _delete_key_from_both(s3, key: str) -> bool:
        deleted = False
        for bucket in (settings.S3_PRIVATE_BUCKET, settings.S3_PUBLIC_BUCKET):
            try:
                await s3.head_object(Bucket=bucket, Key=key)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchKey", "NotFound"):
                    continue
                logger.warning("photo_delete_head_failed", key=key, bucket=bucket, error=str(e), exc_info=True)
                continue
            await s3.delete_object(Bucket=bucket, Key=key)
            deleted = True
            logger.info("Deleted photo", key=key, bucket=bucket)
        return deleted

    @staticmethod
    async def delete_photo(user_id: str, photo_id: str) -> bool:
        """Delete a photo (full + thumbnail, current + legacy keys) from
        whichever bucket it currently lives in."""
        keys: List[str] = []
        for base in PhotoService._candidate_keys(user_id, photo_id):
            keys.append(base)
            keys.append(PhotoService.thumb_key(base))

        deleted = False
        async with _s3_client() as s3:
            for key in keys:
                if await PhotoService._delete_key_from_both(s3, key):
                    deleted = True
        return deleted

    @staticmethod
    async def _move_bucket(s3, src_bucket: str, dst_bucket: str, key: str, public: bool) -> bool:
        """Copy `key` from src to dst, then delete the source. Returns True if
        the object existed and was moved."""
        try:
            await s3.copy_object(
                Bucket=dst_bucket,
                Key=key,
                CopySource={"Bucket": src_bucket, "Key": key},
                **({"ACL": "public-read"} if public else {}),
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        await s3.delete_object(Bucket=src_bucket, Key=key)
        return True

    @staticmethod
    async def publish_photo(user_id: str, photo_id: str) -> None:
        """
        Move a photo (full + thumbnail) from the PRIVATE bucket to the PUBLIC
        bucket. Call this when admin/automated moderation sets status='approved'.

        NOTE: object ACL must be set explicitly to public-read on copy —
        being in a bucket *named* "public" does not make an object public.
        """
        async with _s3_client() as s3:
            moved = False
            for key in PhotoService._candidate_keys(user_id, photo_id):
                if await PhotoService._move_bucket(
                    s3, settings.S3_PRIVATE_BUCKET, settings.S3_PUBLIC_BUCKET, key, public=True
                ):
                    moved = True
                    # Thumbnail is best-effort (older rows may not have one).
                    try:
                        await PhotoService._move_bucket(
                            s3,
                            settings.S3_PRIVATE_BUCKET,
                            settings.S3_PUBLIC_BUCKET,
                            PhotoService.thumb_key(key),
                            public=True,
                        )
                    except Exception as e:
                        logger.warning("photo_publish_thumb_failed", key=key, error=str(e), exc_info=True)
                    break
            if not moved:
                logger.warning("photo_publish_missing", user_id=user_id, photo_id=photo_id)

    @staticmethod
    async def unpublish_photo(user_id: str, photo_id: str) -> None:
        """
        Move a photo (full + thumbnail) from the PUBLIC bucket back to PRIVATE.
        Call this if an approved photo is later rejected/removed.
        """
        async with _s3_client() as s3:
            moved = False
            for key in PhotoService._candidate_keys(user_id, photo_id):
                if await PhotoService._move_bucket(
                    s3, settings.S3_PUBLIC_BUCKET, settings.S3_PRIVATE_BUCKET, key, public=False
                ):
                    moved = True
                    try:
                        await PhotoService._move_bucket(
                            s3,
                            settings.S3_PUBLIC_BUCKET,
                            settings.S3_PRIVATE_BUCKET,
                            PhotoService.thumb_key(key),
                            public=False,
                        )
                    except Exception as e:
                        logger.warning("photo_unpublish_thumb_failed", key=key, error=str(e), exc_info=True)
                    break
            if not moved:
                logger.warning("photo_unpublish_missing", user_id=user_id, photo_id=photo_id)

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
    async def get_photo_thumb_url(key: str, status: str) -> str:
        """Resolve the thumbnail URL for a stored photo key (same bucket/access
        rules as the full image). Legacy JPEG photos have no thumbnail, so fall
        back to the full image."""
        if not key.endswith(".webp"):
            return await PhotoService.get_photo_url(key, status)
        return await PhotoService.get_photo_url(PhotoService.thumb_key(key), status)

    @staticmethod
    def compute_phash(file_data: bytes) -> str:
        """Return a 64-bit dHash hex string for duplicate detection.

        dHash (difference hash) is robust to re-encoding, resizing and small
        crops: identical/near-identical images produce hashes whose Hamming
        distance is tiny, while genuinely different images are far apart.
        """
        img = Image.open(io.BytesIO(file_data))
        # Greyscale + 9x8 so each pixel yields one bit (8 columns of diffs).
        gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        arr = np.asarray(gray, dtype=np.int16)
        diff = arr[:, 1:] > arr[:, :-1]  # (8, 8) booleans
        bits = diff.flatten()
        # Pack 64 bits into a 16-char hex string.
        val = 0
        for b in bits:
            val = (val << 1) | int(b)
        return format(val, "016x")

    @staticmethod
    def hamming(a: str, b: str) -> int:
        """Hamming distance between two hex dHash strings."""
        ia, ib = int(a, 16), int(b, 16)
        return bin(ia ^ ib).count("1")

    @staticmethod
    def is_duplicate(existing_hashes: List[str], new_hash: str) -> bool:
        """True if new_hash is within threshold of any existing photo hash."""
        threshold = settings.PHASH_DUPLICATE_THRESHOLD
        for h in existing_hashes:
            if not h:
                continue
            if PhotoService.hamming(h, new_hash) <= threshold:
                return True
        return False

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
