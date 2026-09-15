# app/services/media_service.py
import io
from typing import Tuple, Optional
from urllib.parse import urlparse
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.services.storage import s3_client

logger = get_logger("media_service")


class MediaService:
    """Handle media uploads for chat using MinIO.

    Images are stored as WebP (much smaller than JPEG at equal quality) and a
    small thumbnail is generated alongside each one so list/bubble views can
    download a tiny file instead of the full-size image.
    """

    MAX_PHOTO_SIZE = settings.MAX_CHAT_PHOTO_SIZE_MB * 1024 * 1024
    MAX_VOICE_SIZE = settings.MAX_CHAT_VOICE_SIZE_MB * 1024 * 1024
    MAX_VOICE_DURATION = settings.MAX_CHAT_VOICE_DURATION
    ALLOWED_IMAGE_FORMATS = [fmt.strip() for fmt in settings.ALLOWED_CHAT_IMAGE_FORMATS.split(",")]

    # Encoding targets — keep storage small without visible quality loss.
    FULL_MAX = 1080
    THUMB_MAX = 320
    IMAGE_QUALITY = 78
    THUMB_QUALITY = 75

    # ── Helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def thumb_key(key: str) -> str:
        """Derive the thumbnail object key from a media key.

        `chat/photos/c/m.webp` -> `chat/photos/c/m_thumb.webp`
        """
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
        """Resize to fit `max_size` and encode to lossy WebP. EXIF is dropped
        (WebP save does not carry it unless passed explicitly)."""
        img = image.copy()
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, "WEBP", quality=quality, method=4)
        return buffer.getvalue()

    @staticmethod
    async def save_photo(file_data: bytes, chat_id: str, message_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Save a chat photo to MinIO as WebP (full + thumbnail).
        Returns: (success, file_key, error_message)
        """
        if len(file_data) > MediaService.MAX_PHOTO_SIZE:
            return False, None, f"Photo too large. Max {MediaService.MAX_PHOTO_SIZE // (1024 * 1024)}MB"

        try:
            image = Image.open(io.BytesIO(file_data))

            if image.format not in MediaService.ALLOWED_IMAGE_FORMATS:
                return False, None, f"Invalid format. Allowed: {', '.join(MediaService.ALLOWED_IMAGE_FORMATS)}"

            rgb = MediaService._to_rgb(image)
            full = MediaService._encode_webp(rgb, MediaService.FULL_MAX, MediaService.IMAGE_QUALITY)
            thumb = MediaService._encode_webp(rgb, MediaService.THUMB_MAX, MediaService.THUMB_QUALITY)

            key = f"chat/photos/{chat_id}/{message_id}.webp"
            thumb_key = MediaService.thumb_key(key)

            async with s3_client() as s3:
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

            # NOTE: we store the object KEY, not a presigned URL. URLs are
            # signed fresh (and cached) at read time via resolve_media_url() —
            # a stored presigned URL would expire after ~15 min and make old
            # messages unopenable.
            logger.info("Uploaded chat photo to MinIO", key=key)
            return True, key, None

        except Exception as e:
            logger.error("Failed to save photo", error=str(e), exc_info=True)
            return False, None, "Invalid image file"

    @staticmethod
    async def save_voice(file_data: bytes, chat_id: str, message_id: str, duration: int) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Save a voice message to MinIO. The client records AAC-LC in an MPEG-4
        (M4A) container, so it is stored as `.m4a` / `audio/mp4` — the format
        ExoPlayer (Android) and AVPlayer (iOS) both play natively.
        Returns: (success, file_key, error_message)
        """
        # Check size
        if len(file_data) > MediaService.MAX_VOICE_SIZE:
            return False, None, f"Voice message too large. Max {MediaService.MAX_VOICE_SIZE // (1024 * 1024)}MB"

        # Check duration
        if duration > MediaService.MAX_VOICE_DURATION:
            return False, None, f"Voice message too long. Max {MediaService.MAX_VOICE_DURATION} seconds"

        try:
            key = f"chat/voice/{chat_id}/{message_id}.m4a"

            async with s3_client() as s3:
                await s3.put_object(
                    Bucket=settings.S3_PRIVATE_BUCKET,
                    Key=key,
                    Body=file_data,
                    ContentType="audio/mp4",
                )

            # Store the object KEY (see save_photo note) — sign at read time.
            logger.info("Uploaded chat voice to MinIO", key=key)
            return True, key, None

        except Exception as e:
            logger.error("Failed to save voice", error=str(e), exc_info=True)
            return False, None, "Failed to save voice message"

    @staticmethod
    async def delete_media(chat_id: str, message_id: str, media_type: str) -> bool:
        """Delete a chat media object (full + thumbnail) from both buckets.

        Handles the current WebP/M4A keys and the legacy JPEG/MP3 keys so old
        messages are cleaned up too.
        """
        if media_type == "photo":
            base_keys = [
                f"chat/photos/{chat_id}/{message_id}.webp",
                f"chat/photos/{chat_id}/{message_id}.jpg",
            ]
        elif media_type == "voice":
            base_keys = [
                f"chat/voice/{chat_id}/{message_id}.m4a",
                f"chat/voice/{chat_id}/{message_id}.mp3",
            ]
        else:
            return False

        keys = list(base_keys) + [MediaService.thumb_key(k) for k in base_keys]

        try:
            async with s3_client() as s3:
                for bucket in (settings.S3_PRIVATE_BUCKET, settings.S3_PUBLIC_BUCKET):
                    for key in keys:
                        try:
                            await s3.delete_object(Bucket=bucket, Key=key)
                        except Exception as delete_err:
                            logger.warning(
                                "chat_media_delete_failed",
                                key=key,
                                bucket=bucket,
                                error=str(delete_err),
                                exc_info=True,
                            )
            logger.info("Deleted chat media", chat_id=chat_id, message_id=message_id, media_type=media_type)
            return True
        except Exception as e:
            logger.error("Failed to delete chat media", error=str(e), exc_info=True)
            return False

    @staticmethod
    async def resolve_media_url(media_ref: Optional[str]) -> Optional[str]:
        """
        Resolve a stored chat-media reference into a fresh, short-lived
        presigned URL for the client to load.

        `media_ref` is the object key (new rows) OR a legacy full URL left over
        from before keys were stored. URLs are signed at read time and cached
        in Redis (~5 min, well under the 15-min expiry) so consecutive loads
        reuse the same URL and the client's image cache keeps hitting.
        """
        if not media_ref:
            return None

        ref = str(media_ref)
        key = ref

        if ref.startswith("http://") or ref.startswith("https://"):
            # Legacy row: media_ref is a full presigned URL whose path encodes
            # the object key: /{bucket}/{key}. Extract the key and re-sign.
            path = urlparse(ref).path.lstrip("/")
            parts = path.split("/", 1)
            key = parts[1] if len(parts) > 1 and parts[1] else parts[0]

        if not key.startswith("chat/"):
            # Not a chat-media key — return as-is (nothing to sign).
            return ref

        cache_key = f"chat_media:{key}"
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return cached
        except Exception as e:
            logger.warning("chat_media_cache_get_failed", key=cache_key, error=str(e))

        try:
            async with s3_client() as s3:
                url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.S3_PRIVATE_BUCKET, "Key": key},
                    ExpiresIn=settings.S3_SIGNED_URL_EXPIRE_SECONDS,
                )
        except Exception as e:
            logger.error("chat_media_presign_failed", key=key, error=str(e), exc_info=True)
            return ref

        try:
            await redis_client.setex(cache_key, 300, url)
        except Exception as e:
            logger.warning("chat_media_cache_set_failed", key=cache_key, error=str(e))

        return url
