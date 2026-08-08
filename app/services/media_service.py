# app/services/media_service.py
import io
from typing import Tuple, Optional
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("media_service")


class MediaService:
    """Handle media uploads for chat using MinIO"""

    MAX_PHOTO_SIZE = settings.MAX_CHAT_PHOTO_SIZE_MB * 1024 * 1024
    MAX_VOICE_SIZE = settings.MAX_CHAT_VOICE_SIZE_MB * 1024 * 1024
    MAX_VOICE_DURATION = settings.MAX_CHAT_VOICE_DURATION
    ALLOWED_IMAGE_FORMATS = [fmt.strip() for fmt in settings.ALLOWED_CHAT_IMAGE_FORMATS.split(",")]


    @staticmethod
    async def save_photo(file_data: bytes, chat_id: str, message_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Save photo message to MinIO.
        Returns: (success, file_url, error_message)
        """
        # Check size
        if len(file_data) > MediaService.MAX_PHOTO_SIZE:
            return False, None, f"Photo too large. Max {MediaService.MAX_PHOTO_SIZE // (1024 * 1024)}MB"

        try:
            # Validate image
            image = Image.open(io.BytesIO(file_data))

            if image.format not in MediaService.ALLOWED_IMAGE_FORMATS:
                return False, None, f"Invalid format. Allowed: {', '.join(MediaService.ALLOWED_IMAGE_FORMATS)}"

            # Convert to RGB if needed
            if image.mode in ('RGBA', 'LA', 'P'):
                rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                rgb_image.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                image = rgb_image

            # Resize if too large (max 1200px)
            max_size = 1200
            if image.width > max_size or image.height > max_size:
                image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            # Save to bytes
            output = io.BytesIO()
            image.save(output, 'JPEG', quality=85, optimize=True)
            file_data = output.getvalue()

            # Upload to MinIO
            import aioboto3
            
            key = f"chat/photos/{chat_id}/{message_id}.jpg"
            
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
            ) as s3:
                await s3.put_object(
                    Bucket=settings.S3_PRIVATE_BUCKET,
                    Key=key,
                    Body=file_data,
                    ContentType="image/jpeg",
                )

            # Generate signed URL
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
            ) as s3:
                url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.S3_PRIVATE_BUCKET, "Key": key},
                    ExpiresIn=settings.S3_SIGNED_URL_EXPIRE_SECONDS,
                )

            logger.info("Uploaded chat photo to MinIO", key=key)
            return True, url, None

        except Exception as e:
            logger.error("Failed to save photo", error=str(e), exc_info=True)
            return False, None, "Invalid image file"

    @staticmethod
    async def save_voice(file_data: bytes, chat_id: str, message_id: str, duration: int) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Save voice message to MinIO.
        Returns: (success, file_url, error_message)
        """
        # Check size
        if len(file_data) > MediaService.MAX_VOICE_SIZE:
            return False, None, f"Voice message too large. Max {MediaService.MAX_VOICE_SIZE // (1024 * 1024)}MB"

        # Check duration
        if duration > MediaService.MAX_VOICE_DURATION:
            return False, None, f"Voice message too long. Max {MediaService.MAX_VOICE_DURATION} seconds"

        try:
            # Upload to MinIO
            import aioboto3
            
            key = f"chat/voice/{chat_id}/{message_id}.mp3"
            
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
            ) as s3:
                await s3.put_object(
                    Bucket=settings.S3_PRIVATE_BUCKET,
                    Key=key,
                    Body=file_data,
                    ContentType="audio/mpeg",
                )

            # Generate signed URL
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
            ) as s3:
                url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.S3_PRIVATE_BUCKET, "Key": key},
                    ExpiresIn=settings.S3_SIGNED_URL_EXPIRE_SECONDS,
                )

            logger.info("Uploaded chat voice to MinIO", key=key)
            return True, url, None

        except Exception as e:
            logger.error("Failed to save voice", error=str(e), exc_info=True)
            return False, None, "Failed to save voice message"

    @staticmethod
    async def delete_media(chat_id: str, message_id: str, media_type: str) -> bool:
        """Delete media file from MinIO"""
        if media_type == "photo":
            key = f"chat/photos/{chat_id}/{message_id}.jpg"
        elif media_type == "voice":
            key = f"chat/voice/{chat_id}/{message_id}.mp3"
        else:
            return False

        try:
            import aioboto3
            
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
            ) as s3:
                # Delete from private bucket
                try:
                    await s3.delete_object(Bucket=settings.S3_PRIVATE_BUCKET, Key=key)
                except Exception as delete_err:
                    logger.warning("chat_media_delete_private_failed", key=key, error=str(delete_err), exc_info=True)

                # Delete from public bucket too
                try:
                    await s3.delete_object(Bucket=settings.S3_PUBLIC_BUCKET, Key=key)
                except Exception as delete_err:
                    logger.warning("chat_media_delete_public_failed", key=key, error=str(delete_err), exc_info=True)
                
            logger.info("Deleted chat media", key=key)
            return True
        except Exception as e:
            logger.error("Failed to delete media", error=str(e), exc_info=True)
            return False
