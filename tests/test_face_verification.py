"""
Tests for face verification (image-based selfie flow).

Uses mocked InsightFace model (no real model download needed).
Tests endpoint logic, Redis state, and the verification pipeline.
"""

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_image_bytes() -> bytes:
    """Create a small synthetic JPEG image."""
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    frame[:] = (100, 150, 200)
    cv2.circle(frame, (320, 240), 120, (200, 180, 160), -1)
    ok, encoded = cv2.imencode(".jpg", frame)
    return encoded.tobytes()


def make_fake_embedding() -> np.ndarray:
    emb = np.random.randn(512).astype(np.float32)
    return emb / np.linalg.norm(emb)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        phone=f"+9891{uuid.uuid4().hex[:10]}",
        email=f"verify_{uuid.uuid4().hex[:8]}@test.com",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserProfile(user_id=user.id, name="Verify User", gender="male"))
    db_session.add(UserSettings(user_id=user.id))
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user: User) -> dict:
    token = create_access_token(str(test_user.id), test_user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def verified_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        phone=f"+9891{uuid.uuid4().hex[:10]}",
        email=f"verified_{uuid.uuid4().hex[:8]}@test.com",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserProfile(
        user_id=user.id, name="Verified", gender="female",
        is_verified=True, verified_at=datetime.now(timezone.utc),
    ))
    db_session.add(UserSettings(user_id=user.id))
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def verified_auth_headers(verified_user: User) -> dict:
    token = create_access_token(str(verified_user.id), verified_user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _add_approved_photo(db_session, user_id):
    photo = Photo(
        user_id=user_id,
        url=f"users/{user_id}/{uuid.uuid4()}.jpg",
        status="approved",
        face_verified=False,
        is_main=True,
    )
    db_session.add(photo)
    return photo


# ---------------------------------------------------------------------------
# Tests: Verification Status
# ---------------------------------------------------------------------------

class TestVerificationStatus:

    @pytest.mark.asyncio
    async def test_status_not_verified(self, client, auth_headers):
        resp = await client.get("/api/v1/users/me/verify/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_verified"] is False
        assert data["eligible_to_verify"] is True

    @pytest.mark.asyncio
    async def test_status_already_verified(self, client, verified_auth_headers):
        resp = await client.get("/api/v1/users/me/verify/status", headers=verified_auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_verified"] is True
        assert data["eligible_to_verify"] is False

    @pytest.mark.asyncio
    async def test_status_with_cooldown(self, client, auth_headers, test_user, patch_redis):
        await patch_redis.setex(f"verify_cooldown:{test_user.id}", 7200, "1")
        resp = await client.get("/api/v1/users/me/verify/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["eligible_to_verify"] is False

    @pytest.mark.asyncio
    async def test_status_no_auth(self, client):
        resp = await client.get("/api/v1/users/me/verify/status")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: Selfie Submission (with mocked face service)
# ---------------------------------------------------------------------------

class TestSelfieSubmission:

    @pytest.mark.asyncio
    async def test_verify_already_verified_matches_keeps_badge(
        self, client, db_session, verified_user, patch_redis
    ):
        """Already-verified user whose selfie still matches stays verified."""
        _add_approved_photo(db_session, verified_user.id)
        await db_session.commit()
        headers = {
            "Authorization": f"Bearer {create_access_token(str(verified_user.id), verified_user.token_version)}"
        }
        fake_emb = make_fake_embedding()
        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.9))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["verified"] is True

        await db_session.refresh(verified_user)
        assert verified_user.profile.is_verified is True

    @pytest.mark.asyncio
    async def test_verify_already_verified_mismatch_revokes_badge(
        self, client, db_session, verified_user, patch_redis
    ):
        """Already-verified user whose photos no longer match loses the badge."""
        _add_approved_photo(db_session, verified_user.id)
        await db_session.commit()
        headers = {
            "Authorization": f"Bearer {create_access_token(str(verified_user.id), verified_user.token_version)}"
        }
        fake_emb = make_fake_embedding()
        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(False, 0.1))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["verified"] is False

        await db_session.refresh(verified_user)
        assert verified_user.profile.is_verified is False
        assert verified_user.profile.verified_at is None

    @pytest.mark.asyncio
    async def test_verify_image_too_large(self, client, auth_headers, test_user, patch_redis):
        fake_large = b"\x00" * ((settings.FACE_VERIFICATION_MAX_SIZE_MB + 1) * 1024 * 1024)
        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("selfie.jpg", fake_large, "image/jpeg")},
        )
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_no_approved_photos(self, client, auth_headers, test_user, patch_redis):
        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(make_fake_embedding(), ""))
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 400
            assert "photo" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_selfie_checks_failed(self, client, auth_headers, db_session, test_user, patch_redis):
        _add_approved_photo(db_session, test_user.id)
        await db_session.commit()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(None, "No face detected in the image"))
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 400
            assert "no face" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_low_similarity(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = _add_approved_photo(db_session, test_user.id)
        await db_session.commit()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(make_fake_embedding(), ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(make_fake_embedding(), ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(False, 0.2))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["verified"] is False
            assert str(photo.id) in data["mismatched_photo_ids"]

    @pytest.mark.asyncio
    async def test_verify_success_auto_approves_pending_photos(
        self, client, auth_headers, db_session, test_user, patch_redis
    ):
        """During onboarding photos are pending; successful selfie verification
        must auto-approve (publish) them so the user becomes visible."""
        photo = Photo(
            user_id=test_user.id,
            url=f"users/{test_user.id}/{uuid.uuid4()}.jpg",
            status="pending",
            face_verified=False,
            is_main=True,
        )
        db_session.add(photo)
        await db_session.commit()

        fake_emb = make_fake_embedding()
        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.85))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_ps.publish_photo = AsyncMock()
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["verified"] is True

        mock_ps.publish_photo.assert_awaited_once()
        await db_session.refresh(photo)
        assert photo.status == "approved"
        assert photo.face_verified is True

    @pytest.mark.asyncio
    async def test_verify_success(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = _add_approved_photo(db_session, test_user.id)
        await db_session.commit()

        fake_emb = make_fake_embedding()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.85))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["verified"] is True
            assert "successfully" in data["message"].lower()

        await db_session.refresh(test_user.profile)
        assert test_user.profile.is_verified is True
        assert test_user.profile.verified_at is not None

        await db_session.refresh(photo)
        assert photo.face_verified is True

        # Face reference cached in Redis
        stored = await patch_redis.get(f"face_ref:{test_user.id}")
        assert stored is not None

    @pytest.mark.asyncio
    async def test_verify_success_sets_cooldown(self, client, auth_headers, db_session, test_user, patch_redis):
        _add_approved_photo(db_session, test_user.id)
        await db_session.commit()

        fake_emb = make_fake_embedding()
        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps, \
             patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_fvs.extract_image_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.extract_single_photo_embedding = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.85))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_nsfw.check_image = AsyncMock(return_value=(True, 0.0))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 200

        exists = await patch_redis.exists(f"verify_cooldown:{test_user.id}")
        assert exists == 1

    @pytest.mark.asyncio
    async def test_verify_no_auth(self, client):
        resp = await client.post(
            "/api/v1/users/me/verify",
            files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_nsfw_selfie(self, client, auth_headers, db_session, test_user, patch_redis):
        _add_approved_photo(db_session, test_user.id)
        await db_session.commit()

        with patch("app.api.v1.endpoints.verify.nsfw_service") as mock_nsfw:
            mock_nsfw.check_image = AsyncMock(return_value=(False, 0.9))
            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("selfie.jpg", make_image_bytes(), "image/jpeg")},
            )
            assert resp.status_code == 400
            assert "content policy" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: Face Verification Service (pure unit tests)
# ---------------------------------------------------------------------------

class TestFaceVerificationService:

    def test_compare_embeddings_high_similarity(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        emb = np.random.randn(128).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        matched, score = svc.compare_embeddings(emb, emb)
        assert matched is True
        assert score > 0.99

    def test_compare_embeddings_low_similarity(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        emb1 = np.random.randn(128).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)
        matched, score = svc.compare_embeddings(emb1, -emb1)
        assert matched is False
        assert score < 0

    def test_compare_embeddings_zero_vector(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        matched, score = svc.compare_embeddings(np.zeros(128, dtype=np.float32), np.random.randn(128).astype(np.float32))
        assert matched is False
        assert score == 0.0

    def test_config_settings_exist(self):
        for attr in (
            "FACE_MODELS_DIR", "FACE_DET_SCORE_THRESHOLD", "FACE_MODEL_THREADS",
            "FACE_MATCH_THRESHOLD",
            "FACE_VERIFICATION_MAX_SIZE_MB", "FACE_VERIFICATION_COOLDOWN_TTL",
            "FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY",
            "FACE_VERIFICATION_MIN_PHOTOS", "FACE_REFERENCE_CACHE_TTL",
        ):
            assert hasattr(settings, attr), attr
