from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
from starlette.requests import Request

from app.core.config import settings
from app.models.photo import Photo
from app.models.user import User
from app.services import face_verification_service, photo_service


def _request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/face-verification/test",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "http_version": "1.1",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
        }
    )


def _upload(content):
    upload = MagicMock()
    upload.read = AsyncMock(return_value=content)
    return upload


class TestFaceVerificationDebugEndpoint:
    async def test_invalid_user_id_stops_before_database_access(self, db_session):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        result = await test_face_verification(
            _request(), _upload(b"img"), "not-a-uuid", db_session, MagicMock()
        )

        assert result == {
            "steps": [
                {
                    "step": "validate_user",
                    "status": "failed",
                    "error": "Invalid user_id format",
                }
            ],
            "success": False,
        }

    async def test_missing_user_stops_after_lookup(self, db_session):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        result = await test_face_verification(
            _request(), _upload(b"img"), str(uuid4()), db_session, MagicMock()
        )

        assert result["steps"][-1]["error"] == "User not found"
        assert result["success"] is False

    async def test_oversized_upload_stops_before_face_extraction(self, db_session, monkeypatch):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        user = User(
            id=uuid4(),
            phone=f"+9891{uuid4().hex[:10]}",
            phone_verified=True,
            is_active=True,
            token_version=1,
            registration_status="onboarding_complete",
        )
        db_session.add(user)
        await db_session.commit()
        monkeypatch.setattr(settings, "FACE_VERIFICATION_MAX_SIZE_MB", 1)
        extract = AsyncMock(side_effect=AssertionError("must not process oversized upload"))
        monkeypatch.setattr(
            face_verification_service.face_verification_service, "extract_image_embedding", extract
        )

        result = await test_face_verification(
            _request(), _upload(b"x" * (1024 * 1024 + 1)), str(user.id), db_session, MagicMock()
        )

        assert result["steps"][-1] == {
            "step": "read_image",
            "status": "failed",
            "error": f"Image too large: {1024 * 1024 + 1} bytes",
        }
        extract.assert_not_awaited()

    async def test_extraction_failure_is_returned_as_step_result(self, db_session, monkeypatch):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        user = User(
            id=uuid4(),
            phone=f"+9891{uuid4().hex[:10]}",
            phone_verified=True,
            is_active=True,
            token_version=1,
            registration_status="onboarding_complete",
        )
        db_session.add(user)
        await db_session.commit()
        extract = AsyncMock(return_value=(None, "No face detected in image"))
        monkeypatch.setattr(
            face_verification_service.face_verification_service, "extract_image_embedding", extract
        )

        result = await test_face_verification(
            _request(), _upload(b"image-bytes"), str(user.id), db_session, MagicMock()
        )

        assert result["steps"][-1] == {
            "step": "extract_image_embedding",
            "status": "failed",
            "error": "No face detected in image",
        }
        extract.assert_awaited_once_with(b"image-bytes")

    async def test_no_approved_photos_stops_before_comparison(self, db_session, monkeypatch):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        user = User(
            id=uuid4(),
            phone=f"+9891{uuid4().hex[:10]}",
            phone_verified=True,
            is_active=True,
            token_version=1,
            registration_status="onboarding_complete",
        )
        db_session.add(user)
        await db_session.commit()
        embedding = np.ones(4, dtype=np.float32)
        extract = AsyncMock(return_value=(embedding, ""))
        monkeypatch.setattr(
            face_verification_service.face_verification_service, "extract_image_embedding", extract
        )

        result = await test_face_verification(
            _request(), _upload(b"image-bytes"), str(user.id), db_session, MagicMock()
        )

        assert result["steps"][-1] == {
            "step": "load_photos",
            "status": "failed",
            "error": "No approved photos found for user",
        }
        assert result["steps"][1]["status"] == "passed"

    async def test_photo_compare_skips_download_failures_and_no_face(self, db_session, monkeypatch):
        from app.api.v1.endpoints.face_verification_debug import test_face_verification

        user = User(
            id=uuid4(),
            phone=f"+9891{uuid4().hex[:10]}",
            phone_verified=True,
            is_active=True,
            token_version=1,
            registration_status="onboarding_complete",
        )
        photos = [
            Photo(id=uuid4(), user_id=user.id, url="fails.jpg", status="approved"),
            Photo(id=uuid4(), user_id=user.id, url="no-face.jpg", status="approved"),
            Photo(id=uuid4(), user_id=user.id, url="match.jpg", status="approved"),
        ]
        db_session.add(user)
        db_session.add_all(photos)
        await db_session.commit()
        extract = AsyncMock(return_value=(np.array([1.0, 0.0]), ""))

        async def extract_photo(image_bytes):
            if image_bytes == b"no-face":
                return None, "no face"
            return np.array([1.0, 0.0]), ""

        compare = MagicMock(return_value=(True, 0.98765))

        async def download(photo_key):
            if photo_key == "fails.jpg":
                raise RuntimeError("storage down")
            return b"no-face" if photo_key == "no-face.jpg" else b"match"

        downloads = AsyncMock(side_effect=download)
        single_photo = AsyncMock(side_effect=extract_photo)
        monkeypatch.setattr(
            face_verification_service.face_verification_service, "extract_image_embedding", extract
        )
        monkeypatch.setattr(
            face_verification_service.face_verification_service,
            "extract_single_photo_embedding",
            single_photo,
        )
        monkeypatch.setattr(
            face_verification_service.face_verification_service, "compare_embeddings", compare
        )
        monkeypatch.setattr(photo_service.PhotoService, "download_photo_bytes", downloads)

        result = await test_face_verification(
            _request(), _upload(b"selfie"), str(user.id), db_session, MagicMock()
        )

        load_step = next(step for step in result["steps"] if step["step"] == "load_photos")
        compare_step = next(
            step for step in result["steps"] if step["step"] == "compare_embeddings"
        )
        assert compare_step["status"] == "passed"
        assert load_step["photo_count"] == 3
        assert {result["photo_id"]: result for result in compare_step["per_photo"]} == {
            str(photos[1].id): {"photo_id": str(photos[1].id), "status": "no_face"},
            str(photos[2].id): {
                "photo_id": str(photos[2].id),
                "matched": True,
                "similarity_score": 0.9877,
            },
        }
        assert compare_step["best_similarity_score"] == 0.9877
        assert result["success"] is True
        assert result["summary"] == {
            "similarity_score": 0.9877,
            "threshold": settings.FACE_MATCH_THRESHOLD,
            "face_match": True,
            "would_verify": True,
        }
        compare.assert_called_once()
