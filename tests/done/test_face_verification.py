"""
Tests for face verification feature.

Uses mocked InsightFace model (no real model download needed).
Tests endpoint logic, challenge flow, Redis state, and verification pipeline.
"""

import io
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.models.photo import Photo
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_video_bytes(duration_sec: int = 5, fps: int = 30) -> bytes:
    """Create a synthetic MP4 video."""
    import tempfile, os
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        out = cv2.VideoWriter(tmp.name, fourcc, fps, (640, 480))
        for i in range(fps * duration_sec):
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame[:] = (100, 150, 200)
            cx = 320 + int(30 * np.sin(i / fps))
            cv2.circle(frame, (cx, 240), 80, (200, 180, 160), -1)
            out.write(frame)
        out.release()
        with open(tmp.name, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_user(db_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"verify_{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass123"),
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
        email=f"verified_{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass123"),
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


# ---------------------------------------------------------------------------
# Tests: Challenge Generation
# ---------------------------------------------------------------------------

class TestChallengeGeneration:

    @pytest.mark.asyncio
    async def test_generate_challenge_success(self, client, auth_headers):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["challenge_type"] in ("blink", "turn_left", "turn_right", "smile", "nod")
        assert "instructions" in data
        assert "challenge_id" in data

    @pytest.mark.asyncio
    async def test_generate_challenge_stores_in_redis(self, client, auth_headers, test_user, patch_redis):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        key = f"verify_challenge:{test_user.id}"
        stored = await patch_redis.get(key)
        assert stored is not None
        challenge = json.loads(stored)
        assert challenge["challenge_id"] == data["challenge_id"]
        assert challenge["challenge_type"] == data["challenge_type"]
        assert challenge["status"] == "pending"

    @pytest.mark.asyncio
    async def test_generate_challenge_already_verified(self, client, verified_auth_headers):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=verified_auth_headers)
        assert resp.status_code == 400
        assert "already verified" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_generate_challenge_no_auth(self, client):
        resp = await client.post("/api/v1/users/me/verify/challenge")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_generate_challenge_cooldown(self, client, auth_headers, test_user, patch_redis):
        await patch_redis.setex(f"verify_cooldown:{test_user.id}", 3600, "1")
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        assert resp.status_code == 429
        assert "wait" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_generate_challenge_daily_limit(self, client, auth_headers, test_user, patch_redis):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await patch_redis.set(
            f"verify_attempts:{test_user.id}:{today}",
            str(settings.FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY),
        )
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        assert resp.status_code == 429
        assert "maximum" in resp.json()["detail"].lower()


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
# Tests: Video Submission (with mocked face service)
# ---------------------------------------------------------------------------

class TestVideoSubmission:

    @pytest.mark.asyncio
    async def test_verify_no_challenge_id(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
            params={"challenge_id": "", "challenge_type": "blink"},
        )
        assert resp.status_code == 400
        assert "challenge_id" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_verify_challenge_expired(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
            params={"challenge_id": str(uuid.uuid4()), "challenge_type": "blink"},
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_challenge_type_mismatch(self, client, auth_headers, test_user, patch_redis):
        challenge_id = str(uuid.uuid4())
        challenge_data = json.dumps({
            "challenge_id": challenge_id,
            "challenge_type": "blink",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        })
        await patch_redis.set(
            f"verify_challenge:{test_user.id}",
            challenge_data,
            ex=300,
        )

        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
            params={
                "challenge_id": challenge_id,
                "challenge_type": "turn_left",
            },
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_video_too_short(self, client, auth_headers, test_user, patch_redis):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("video.mp4", make_video_bytes(duration_sec=2), "video/mp4")},
            params={
                "challenge_id": challenge["challenge_id"],
                "challenge_type": challenge["challenge_type"],
            },
        )
        assert resp.status_code == 400
        assert "too short" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_video_too_large(self, client, auth_headers, test_user, patch_redis):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        fake_large = b"\x00" * (100 * 1024 * 1024)
        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=auth_headers,
            files={"file": ("video.mp4", fake_large, "video/mp4")},
            params={
                "challenge_id": challenge["challenge_id"],
                "challenge_type": challenge["challenge_type"],
            },
        )
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_already_verified(self, client, verified_auth_headers):
        resp = await client.post(
            "/api/v1/users/me/verify",
            headers=verified_auth_headers,
            files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
            params={"challenge_id": "x", "challenge_type": "blink"},
        )
        assert resp.status_code == 400
        assert "already verified" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_no_approved_photos(self, client, auth_headers, test_user, patch_redis):
        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs:
            mock_fvs.process_video = AsyncMock(return_value=([np.zeros((640, 640, 3), dtype=np.uint8)] * 5, ""))
            mock_fvs.validate_challenge = AsyncMock(return_value=(True, "OK"))
            mock_fvs.extract_video_embeddings = AsyncMock(
                return_value=(np.random.randn(512).astype(np.float32), "")
            )

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
                params={
                    "challenge_id": challenge["challenge_id"],
                    "challenge_type": challenge["challenge_type"],
                },
            )
            assert resp.status_code == 400
            assert "photo" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_liveness_failed(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = Photo(
            user_id=test_user.id, url=f"users/{test_user.id}/{uuid.uuid4()}.jpg",
            status="approved", face_verified=True, is_main=True,
        )
        db_session.add(photo)
        await db_session.commit()

        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps:
            mock_fvs.process_video = AsyncMock(return_value=([np.zeros((640, 640, 3), dtype=np.uint8)] * 5, ""))
            mock_fvs.validate_challenge = AsyncMock(return_value=(False, "No blink detected"))
            mock_fvs.extract_video_embeddings = AsyncMock(
                return_value=(np.random.randn(512).astype(np.float32), "")
            )
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
                params={
                    "challenge_id": challenge["challenge_id"],
                    "challenge_type": challenge["challenge_type"],
                },
            )
            assert resp.status_code == 400
            assert "liveness" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_low_similarity(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = Photo(
            user_id=test_user.id, url=f"users/{test_user.id}/{uuid.uuid4()}.jpg",
            status="approved", face_verified=True, is_main=True,
        )
        db_session.add(photo)
        await db_session.commit()

        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps:
            mock_fvs.process_video = AsyncMock(return_value=([np.zeros((640, 640, 3), dtype=np.uint8)] * 5, ""))
            mock_fvs.validate_challenge = AsyncMock(return_value=(True, "OK"))
            mock_fvs.extract_video_embeddings = AsyncMock(
                return_value=(np.random.randn(512).astype(np.float32), "")
            )
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_fvs.extract_photo_embeddings = AsyncMock(
                return_value=(np.random.randn(512).astype(np.float32), "")
            )
            mock_fvs.compare_embeddings = MagicMock(return_value=(False, 0.2))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
                params={
                    "challenge_id": challenge["challenge_id"],
                    "challenge_type": challenge["challenge_type"],
                },
            )
            assert resp.status_code == 400
            assert "failed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_verify_success(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = Photo(
            user_id=test_user.id, url=f"users/{test_user.id}/{uuid.uuid4()}.jpg",
            status="approved", face_verified=False, is_main=True,
        )
        db_session.add(photo)
        await db_session.commit()

        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps:
            fake_emb = np.random.randn(512).astype(np.float32)
            fake_emb = fake_emb / np.linalg.norm(fake_emb)

            mock_fvs.process_video = AsyncMock(return_value=([np.zeros((640, 640, 3), dtype=np.uint8)] * 5, ""))
            mock_fvs.validate_challenge = AsyncMock(return_value=(True, "OK"))
            mock_fvs.extract_video_embeddings = AsyncMock(return_value=(fake_emb, ""))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_fvs.extract_photo_embeddings = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.85))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
                params={
                    "challenge_id": challenge["challenge_id"],
                    "challenge_type": challenge["challenge_type"],
                },
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

    @pytest.mark.asyncio
    async def test_verify_success_sets_cooldown(self, client, auth_headers, db_session, test_user, patch_redis):
        photo = Photo(
            user_id=test_user.id, url=f"users/{test_user.id}/{uuid.uuid4()}.jpg",
            status="approved", face_verified=False, is_main=True,
        )
        db_session.add(photo)
        await db_session.commit()

        resp = await client.post("/api/v1/users/me/verify/challenge", headers=auth_headers)
        challenge = resp.json()

        with patch("app.api.v1.endpoints.verify.face_verification_service") as mock_fvs, \
             patch("app.api.v1.endpoints.verify.PhotoService") as mock_ps:
            fake_emb = np.random.randn(512).astype(np.float32)
            fake_emb = fake_emb / np.linalg.norm(fake_emb)
            mock_fvs.process_video = AsyncMock(return_value=([np.zeros((640, 640, 3), dtype=np.uint8)] * 5, ""))
            mock_fvs.validate_challenge = AsyncMock(return_value=(True, "OK"))
            mock_fvs.extract_video_embeddings = AsyncMock(return_value=(fake_emb, ""))
            mock_ps.download_photo_bytes = AsyncMock(return_value=b"fake")
            mock_fvs.extract_photo_embeddings = AsyncMock(return_value=(fake_emb, ""))
            mock_fvs.compare_embeddings = MagicMock(return_value=(True, 0.85))

            resp = await client.post(
                "/api/v1/users/me/verify",
                headers=auth_headers,
                files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
                params={
                    "challenge_id": challenge["challenge_id"],
                    "challenge_type": challenge["challenge_type"],
                },
            )
            assert resp.status_code == 200

        exists = await patch_redis.exists(f"verify_cooldown:{test_user.id}")
        assert exists == 1

    @pytest.mark.asyncio
    async def test_verify_no_auth(self, client):
        resp = await client.post(
            "/api/v1/users/me/verify",
            files={"file": ("video.mp4", make_video_bytes(), "video/mp4")},
            params={"challenge_id": "x", "challenge_type": "blink"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: Face Verification Service (pure unit tests)
# ---------------------------------------------------------------------------

class TestFaceVerificationService:

    def test_compare_embeddings_high_similarity(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        matched, score = svc.compare_embeddings(emb, emb)
        assert matched is True
        assert score > 0.99

    def test_compare_embeddings_low_similarity(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        emb1 = np.random.randn(512).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)
        matched, score = svc.compare_embeddings(emb1, -emb1)
        assert matched is False
        assert score < 0

    def test_compare_embeddings_zero_vector(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        matched, score = svc.compare_embeddings(np.zeros(512, dtype=np.float32), np.random.randn(512).astype(np.float32))
        assert matched is False
        assert score == 0.0

    def test_ear_calculation(self):
        from app.services.face_verification_service import FaceVerificationService
        svc = FaceVerificationService()
        landmarks = np.zeros((68, 3), dtype=np.float64)
        landmarks[36] = [100, 200, 0]
        landmarks[37] = [110, 190, 0]
        landmarks[38] = [130, 190, 0]
        landmarks[39] = [140, 200, 0]
        landmarks[40] = [130, 210, 0]
        landmarks[41] = [110, 210, 0]
        ear = svc._compute_ear(landmarks)
        assert 0.0 <= ear <= 1.0

    def test_challenge_types_defined(self):
        from app.services.face_verification_service import CHALLENGE_TYPES
        assert len(CHALLENGE_TYPES) == 5
        for ct in ("blink", "turn_left", "turn_right", "smile", "nod"):
            assert ct in CHALLENGE_TYPES

    def test_config_settings_exist(self):
        for attr in (
            "FACE_MATCH_THRESHOLD", "FACE_VERIFICATION_MODEL",
            "FACE_VERIFICATION_FRAME_RATE", "FACE_VERIFICATION_VIDEO_MIN_SECONDS",
            "FACE_VERIFICATION_VIDEO_MAX_SECONDS", "FACE_VERIFICATION_CHALLENGE_TTL",
            "FACE_VERIFICATION_COOLDOWN_TTL", "FACE_VERIFICATION_MAX_ATTEMPTS_PER_DAY",
            "FACE_VERIFICATION_BLINK_THRESHOLD", "FACE_VERIFICATION_TURN_THRESHOLD",
            "FACE_VERIFICATION_SMILE_THRESHOLD", "FACE_VERIFICATION_NOD_THRESHOLD",
        ):
            assert hasattr(settings, attr)
