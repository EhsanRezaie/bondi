import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from app.core.config import settings
from app.services import media_service, photo_service
from app.services.media_service import MediaService, MediaValidationError
from app.services.photo_service import PhotoService


def _image_bytes(fmt="JPEG", size=(1200, 800), mode="RGB"):
    color = (20, 80, 140, 255) if mode == "RGBA" else (20, 80, 140)
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


class _S3Context:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return None


def _missing(code="404"):
    return ClientError({"Error": {"Code": code}}, "HeadObject")


class TestPhotoServiceUnitPaths:
    def test_keys_rgb_encoding_and_phash_helpers(self):
        assert PhotoService._object_key("u", "p") == "users/u/p.webp"
        assert PhotoService._legacy_object_key("u", "p") == "users/u/p.jpg"
        assert PhotoService._candidate_keys("u", "p") == [
            "users/u/p.webp",
            "users/u/p.jpg",
        ]
        assert PhotoService.thumb_key("users/u/p.webp") == "users/u/p_thumb.webp"
        assert PhotoService.thumb_key("plain") == "plain_thumb"

        rgba = Image.new("RGBA", (1200, 800), (255, 0, 0, 80))
        rgb = PhotoService._to_rgb(rgba)
        assert rgb.mode == "RGB"
        assert rgb.size == rgba.size
        encoded = PhotoService._encode_webp(rgb, 320, 75)
        with Image.open(io.BytesIO(encoded)) as decoded:
            assert decoded.format == "WEBP"
            assert decoded.size == (320, 213)

        data = _image_bytes()
        phash = PhotoService.compute_phash(data)
        assert len(phash) == 16
        assert PhotoService.hamming(phash, phash) == 0
        assert PhotoService.is_duplicate(["", phash], phash) is True
        assert PhotoService.is_duplicate([], phash) is False

    async def test_validate_size_format_dimensions_and_corrupt_data(self):
        ok, error = await PhotoService.validate_image(
            b"x" * (PhotoService.MAX_FILE_SIZE + 1), "large.jpg"
        )
        assert ok is False and "too large" in error

        assert PhotoService._validate_image_sync(_image_bytes(fmt="GIF"))[0] is False
        assert PhotoService._validate_image_sync(_image_bytes(size=(100, 300)))[0] is False
        assert PhotoService._validate_image_sync(_image_bytes(size=(5100, 300)))[0] is False
        assert PhotoService._validate_image_sync(_image_bytes(size=(1500, 300)))[0] is False
        assert PhotoService._validate_image_sync(b"garbage") == (
            False,
            "Invalid or corrupted image file",
        )
        assert PhotoService._validate_image_sync(_image_bytes(size=(500, 500))) == (True, None)

    async def test_save_photo_puts_webp_full_and_thumbnail_to_private(self, monkeypatch):
        s3 = MagicMock()
        s3.put_object = AsyncMock()
        monkeypatch.setattr(photo_service, "_s3_client", lambda: _S3Context(s3))

        key = await PhotoService.save_photo("user-1", "photo-2", _image_bytes())

        assert key == "users/user-1/photo-2.webp"
        assert [call.kwargs["Key"] for call in s3.put_object.await_args_list] == [
            key,
            "users/user-1/photo-2_thumb.webp",
        ]
        assert all(
            call.kwargs["Bucket"] == settings.S3_PRIVATE_BUCKET
            for call in s3.put_object.await_args_list
        )
        assert all(
            call.kwargs["ContentType"] == "image/webp" for call in s3.put_object.await_args_list
        )

    async def test_delete_checks_buckets_and_current_plus_legacy_keys(self, monkeypatch):
        s3 = MagicMock()
        s3.head_object = AsyncMock()
        s3.delete_object = AsyncMock()
        s3.head_object.side_effect = [
            None,
            _missing(),
            _missing("NoSuchKey"),
            _missing("NotFound"),
            _missing(),
            _missing(),
            _missing(),
            _missing(),
        ]
        monkeypatch.setattr(photo_service, "_s3_client", lambda: _S3Context(s3))

        deleted = await PhotoService.delete_photo("u", "p")

        assert deleted is True
        s3.delete_object.assert_awaited_once_with(
            Bucket=settings.S3_PRIVATE_BUCKET, Key="users/u/p.webp"
        )

    async def test_delete_key_handles_non_not_found_head_errors(self):
        s3 = MagicMock()
        s3.head_object = AsyncMock(
            side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
        )
        s3.delete_object = AsyncMock()

        assert await PhotoService._delete_key_from_both(s3, "k") is False
        assert s3.head_object.await_count == 2
        s3.delete_object.assert_not_awaited()

    async def test_move_bucket_missing_and_copy_success(self):
        s3 = MagicMock()
        s3.copy_object = AsyncMock(side_effect=_missing())
        s3.delete_object = AsyncMock()
        assert await PhotoService._move_bucket(s3, "a", "b", "k", True) is False
        s3.delete_object.assert_not_awaited()

        s3.copy_object.side_effect = None
        assert await PhotoService._move_bucket(s3, "a", "b", "k", True) is True
        assert s3.copy_object.await_args.kwargs["ACL"] == "public-read"
        s3.delete_object.assert_awaited_once_with(Bucket="a", Key="k")

    async def test_publish_unpublish_legacy_fallback_and_thumbnail_best_effort(self, monkeypatch):
        s3 = MagicMock()
        s3.copy_object = AsyncMock()
        s3.delete_object = AsyncMock()
        monkeypatch.setattr(photo_service, "_s3_client", lambda: _S3Context(s3))
        move = AsyncMock(side_effect=[False, True, RuntimeError("thumbnail unavailable")])
        monkeypatch.setattr(PhotoService, "_move_bucket", move)

        await PhotoService.publish_photo("u", "p")

        assert move.await_args_list[0].args[3] == "users/u/p.webp"
        assert move.await_args_list[1].args[3] == "users/u/p.jpg"
        assert move.await_args_list[2].args[3] == "users/u/p_thumb.jpg"
        assert move.await_args_list[1].kwargs["public"] is True

        move.reset_mock(side_effect=True)
        move.side_effect = [False, True, True]
        await PhotoService.unpublish_photo("u", "p")
        assert move.await_args_list[1].args[1:3] == (
            settings.S3_PUBLIC_BUCKET,
            settings.S3_PRIVATE_BUCKET,
        )
        assert move.await_args_list[1].kwargs["public"] is False

    async def test_photo_url_status_cache_signed_url_and_thumbnail_fallback(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        monkeypatch.setattr(photo_service, "redis_client", redis)
        s3 = MagicMock()
        s3.generate_presigned_url = AsyncMock(return_value="https://signed.test/object")
        monkeypatch.setattr(photo_service, "_s3_client", lambda: _S3Context(s3))

        public_url = await PhotoService.get_photo_url("k.webp", "approved")
        assert public_url == f"{settings.S3_PUBLIC_BASE_URL}/k.webp"
        s3.generate_presigned_url.assert_not_awaited()

        signed = await PhotoService.get_photo_url("k.webp", "pending")
        assert signed == "https://signed.test/object"
        redis.setex.assert_awaited_once_with("presign:k.webp", 300, signed)
        assert await PhotoService.get_photo_thumb_url("legacy.jpg", "pending") == signed
        redis.get.return_value = "cached-url"
        assert await PhotoService.get_photo_url("cached.webp", "rejected") == "cached-url"

    async def test_download_public_then_private_and_raises_on_both_failures(self, monkeypatch):
        body = MagicMock()
        body.read = AsyncMock(return_value=b"public-bytes")
        s3 = MagicMock()
        s3.get_object = AsyncMock(return_value={"Body": body})
        monkeypatch.setattr(photo_service, "_s3_client", lambda: _S3Context(s3))

        assert await PhotoService.download_photo_bytes("k") == b"public-bytes"
        assert s3.get_object.await_args.kwargs["Bucket"] == settings.S3_PUBLIC_BUCKET

        private_body = MagicMock()
        private_body.read = AsyncMock(return_value=b"private-bytes")
        s3.get_object.side_effect = [
            _missing(),
            {"Body": private_body},
        ]
        assert await PhotoService.download_photo_bytes("k") == b"private-bytes"
        s3.get_object.side_effect = [_missing(), _missing()]
        with pytest.raises(ClientError):
            await PhotoService.download_photo_bytes("k")


class TestMediaServiceUnitPaths:
    def test_image_encoding_helpers_cover_modes_and_validation(self):
        assert MediaService.thumb_key("chat/photos/c/m.webp") == "chat/photos/c/m_thumb.webp"
        assert MediaService.thumb_key("m") == "m_thumb"

        for mode in ("RGBA", "L", "RGB"):
            rgb = MediaService._to_rgb(Image.new(mode, (1100, 700), 50))
            assert rgb.mode == "RGB"
        full, thumb = MediaService._encode_photo_sync(_image_bytes(fmt="PNG", mode="RGBA"))
        with (
            Image.open(io.BytesIO(full)) as full_image,
            Image.open(io.BytesIO(thumb)) as thumb_image,
        ):
            assert full_image.format == "WEBP" and full_image.size == (1080, 720)
            assert thumb_image.format == "WEBP" and thumb_image.size == (320, 213)

        with pytest.raises(MediaValidationError, match="Invalid format"):
            MediaService._encode_photo_sync(_image_bytes(fmt="GIF"))
        with pytest.raises(MediaValidationError, match="Invalid image file"):
            MediaService._encode_photo_sync(b"corrupt")

    async def test_save_photo_size_decode_and_upload_paths(self, monkeypatch):
        ok, key, error = await MediaService.save_photo(
            b"x" * (MediaService.MAX_PHOTO_SIZE + 1), "c", "m"
        )
        assert (ok, key) == (False, None)
        assert "too large" in error

        ok, key, error = await MediaService.save_photo(b"invalid", "c", "m")
        assert (ok, key) == (False, None)
        assert error == "Invalid image file"

        s3 = MagicMock()
        s3.put_object = AsyncMock()
        monkeypatch.setattr(media_service, "s3_client", lambda: _S3Context(s3))
        ok, key, error = await MediaService.save_photo(_image_bytes(), "c", "m")
        assert (ok, key, error) == (True, "chat/photos/c/m.webp", None)
        assert [call.kwargs["Key"] for call in s3.put_object.await_args_list] == [
            "chat/photos/c/m.webp",
            "chat/photos/c/m_thumb.webp",
        ]

    async def test_voice_validation_upload_and_failure_paths(self, monkeypatch):
        ok, key, error = await MediaService.save_voice(
            b"x" * (MediaService.MAX_VOICE_SIZE + 1), "c", "m", 1
        )
        assert not ok and key is None and "too large" in error
        ok, key, error = await MediaService.save_voice(
            b"voice", "c", "m", MediaService.MAX_VOICE_DURATION + 1
        )
        assert not ok and key is None and "too long" in error

        s3 = MagicMock()
        s3.put_object = AsyncMock()
        monkeypatch.setattr(media_service, "s3_client", lambda: _S3Context(s3))
        assert await MediaService.save_voice(b"voice", "c", "m", 5) == (
            True,
            "chat/voice/c/m.m4a",
            None,
        )
        assert s3.put_object.await_args.kwargs["ContentType"] == "audio/mp4"

        s3.put_object.side_effect = RuntimeError("storage failure")
        assert await MediaService.save_voice(b"voice", "c", "m", 5) == (
            False,
            None,
            "Failed to save voice message",
        )

    async def test_delete_media_known_unknown_and_outer_failure(self, monkeypatch):
        s3 = MagicMock()
        s3.delete_object = AsyncMock()
        monkeypatch.setattr(media_service, "s3_client", lambda: _S3Context(s3))

        assert await MediaService.delete_media("c", "m", "unknown") is False
        assert await MediaService.delete_media("c", "m", "photo") is True
        assert s3.delete_object.await_count == 8
        assert any(call.kwargs["Key"].endswith(".jpg") for call in s3.delete_object.await_args_list)
        assert await MediaService.delete_media("c", "m", "voice") is True
        assert any(call.kwargs["Key"].endswith(".mp3") for call in s3.delete_object.await_args_list)

        monkeypatch.setattr(
            media_service,
            "s3_client",
            lambda: (_ for _ in ()).throw(RuntimeError("client unavailable")),
        )
        assert await MediaService.delete_media("c", "m", "photo") is False

    async def test_resolve_url_cache_legacy_nonmedia_and_sign_failure(self, monkeypatch):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        monkeypatch.setattr(media_service, "redis_client", redis)
        s3 = MagicMock()
        s3.generate_presigned_url = AsyncMock(return_value="https://signed.test/media")
        monkeypatch.setattr(media_service, "s3_client", lambda: _S3Context(s3))

        assert await MediaService.resolve_media_url(None) is None
        assert (
            await MediaService.resolve_media_url("https://example.test/avatar.jpg")
            == "https://example.test/avatar.jpg"
        )
        assert (
            await MediaService.resolve_media_url(
                "https://old.test/private/chat/photos/c/m.jpg?sig=x"
            )
            == "https://signed.test/media"
        )
        assert s3.generate_presigned_url.await_args.kwargs["Params"]["Key"] == "chat/photos/c/m.jpg"
        assert redis.setex.await_args.args == (
            "chat_media:chat/photos/c/m.jpg",
            300,
            "https://signed.test/media",
        )

        redis.get.return_value = "cached-media-url"
        assert await MediaService.resolve_media_url("chat/photos/c/m.jpg") == "cached-media-url"

        redis.get.side_effect = RuntimeError("cache unavailable")
        redis.setex.side_effect = RuntimeError("cache unavailable")
        assert (
            await MediaService.resolve_media_url("chat/voice/c/m.m4a")
            == "https://signed.test/media"
        )

        monkeypatch.setattr(
            media_service,
            "s3_client",
            lambda: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
        )
        assert (
            await MediaService.resolve_media_url("chat/photos/c/m.webp") == "chat/photos/c/m.webp"
        )
