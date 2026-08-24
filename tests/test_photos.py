"""
Tests for photo upload, retrieval, deletion, and admin moderation.

Runs against REAL test infrastructure (per docker-compose_test.yml):
  - Real Postgres (db_test)
  - Real Redis (redis_test)
  - Real MinIO (minio_test) — NOT mocked. We genuinely upload bytes,
    fetch signed/public URLs over HTTP, and verify the objects move
    between the photos-private-test / photos-public-test buckets.

Before running:
    docker compose -f docker-compose_test.yml up -d
    # wait for minio_test_init to finish (creates buckets + sets
    # photos-public-test to public-read)
    pytest tests/test_photos.py -v
"""

import io
import uuid

import httpx
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

from app.core.security import create_access_token
from app.core.config import settings
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings
from app.models.photo import Photo


ADMIN_HEADERS = {"X-Admin-Key": settings.ADMIN_SECRET_KEY}


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def make_image_bytes(width=800, height=800, fmt="JPEG") -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def make_upload_file(width=800, height=800, fmt="JPEG", filename="photo.jpg"):
    data = make_image_bytes(width, height, fmt)
    content_type = f"image/{fmt.lower()}"
    return {"file": (filename, data, content_type)}


# ---------------------------------------------------------------------------
# User + auth fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_user(db_session) -> User:
    """A regular, non-admin user with a complete profile (so they could
    plausibly upload photos in the real app flow)."""
    user = User(
        id=uuid.uuid4(),
        phone=f"+9891{uuid.uuid4().hex[:10]}",
        email=f"user_{uuid.uuid4().hex[:8]}@test.com",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )
    db_session.add(user)
    await db_session.flush()

    profile = UserProfile(user_id=user.id, name="Test User", gender="male")
    settings_row = UserSettings(user_id=user.id)
    db_session.add(profile)
    db_session.add(settings_row)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user: User) -> dict:
    token = create_access_token(str(test_user.id), test_user.token_version)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def second_user(db_session) -> User:
    """A second user, used to test that users can't touch each other's photos."""
    user = User(
        id=uuid.uuid4(),
        phone=f"+9891{uuid.uuid4().hex[:10]}",
        email=f"user2_{uuid.uuid4().hex[:8]}@test.com",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )
    db_session.add(user)
    await db_session.flush()
    profile = UserProfile(user_id=user.id, name="Second User", gender="male")
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def second_auth_headers(second_user: User) -> dict:
    token = create_access_token(str(second_user.id), second_user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def upload_and_get_id(client: AsyncClient, auth_headers: dict, **img_kwargs) -> dict:
    """Helper: upload one valid photo, return the JSON response."""
    files = make_upload_file(**img_kwargs)
    resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture(autouse=True)
async def _mock_face_check():
    """Mock face embedding extraction so uploads never load the real InsightFace model."""
    with patch(
        "app.api.v1.endpoints.photos.face_verification_service.extract_single_photo_embedding",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = (np.random.randn(512).astype(np.float32), "")
        yield m


# ===========================================================================
# UPLOAD
# ===========================================================================

class TestUploadPhoto:

    async def test_upload_valid_photo_succeeds(self, client, auth_headers):
        files = make_upload_file()
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert "url" in body and body["url"]
        assert body["message"] == "Photo uploaded. Under review by admin."

    async def test_upload_rejects_no_face(self, client, auth_headers, _mock_face_check):
        """Photos without a detectable face (nature/objects) are auto-rejected."""
        _mock_face_check.return_value = (None, "No face detected in the photo")
        files = make_upload_file()
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "no face" in resp.json()["detail"].lower()

    async def test_upload_requires_auth(self, client):
        files = make_upload_file()
        resp = await client.post("/api/v1/users/me/photos", files=files)
        assert resp.status_code == 401

    async def test_upload_rejects_duplicate_image(self, client, auth_headers):
        """The same image cannot be uploaded twice (dHash duplicate check)."""
        first = await client.post(
            "/api/v1/users/me/photos",
            headers=auth_headers,
            files=make_upload_file(),
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/users/me/photos",
            headers=auth_headers,
            files=make_upload_file(),
        )
        assert second.status_code == 400
        assert "already uploaded" in second.json()["detail"].lower()

    async def test_upload_allows_different_image(self, client, auth_headers):
        """A genuinely different image uploads fine after a previous one."""
        first = await client.post(
            "/api/v1/users/me/photos",
            headers=auth_headers,
            files=make_upload_file(),
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/users/me/photos",
            headers=auth_headers,
            files=make_upload_file(width=801, height=801, filename="other.jpg"),
        )
        assert second.status_code == 201, second.text

    async def test_upload_rejects_too_small_image(self, client, auth_headers):
        files = make_upload_file(width=50, height=50)
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "too small" in resp.json()["detail"].lower()

    async def test_upload_rejects_too_large_dimensions(self, client, auth_headers):
        files = make_upload_file(width=6000, height=6000)
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    async def test_upload_rejects_extreme_aspect_ratio(self, client, auth_headers):
        files = make_upload_file(width=1500, height=300)  # ratio = 5.0
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "aspect ratio" in resp.json()["detail"].lower()

    async def test_upload_rejects_invalid_format(self, client, auth_headers):
        # A GIF is not in ALLOWED_FORMATS (JPEG, PNG, WEBP)
        img = Image.new("RGB", (800, 800), color=(1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, "GIF")
        files = {"file": ("photo.gif", buf.getvalue(), "image/gif")}
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "format" in resp.json()["detail"].lower()

    async def test_upload_rejects_corrupted_file(self, client, auth_headers):
        files = {"file": ("photo.jpg", b"not a real image file", "image/jpeg")}
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400

    async def test_first_uploaded_photo_becomes_main(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photos = get_resp.json()
        assert len(photos) == 1
        assert photos[0]["is_main"] is True

    async def test_second_uploaded_photo_is_not_main(self, client, auth_headers):
        await upload_and_get_id(client, auth_headers)
        await upload_and_get_id(client, auth_headers)
        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photos = sorted(get_resp.json(), key=lambda p: p["order"])
        assert photos[0]["is_main"] is True
        assert photos[1]["is_main"] is False

    async def test_upload_enforces_max_photos(self, client, auth_headers):
        for _ in range(9):
            body = await upload_and_get_id(client, auth_headers)
        files = make_upload_file()
        resp = await client.post("/api/v1/users/me/photos", headers=auth_headers, files=files)
        assert resp.status_code == 400
        assert "maximum 9" in resp.json()["detail"].lower()

    async def test_uploaded_photo_url_is_a_real_signed_url_and_is_fetchable(self, client, auth_headers):
        """End-to-end against REAL MinIO: the URL returned on upload must
        actually be fetchable and return real image bytes — not just be
        present as a string."""
        body = await upload_and_get_id(client, auth_headers)
        async with httpx.AsyncClient() as raw_client:
            img_resp = await raw_client.get(body["url"])
        assert img_resp.status_code == 200, f"Signed URL not fetchable: {img_resp.status_code}"
        assert img_resp.headers["content-type"].startswith("image/")
        assert len(img_resp.content) > 0


# ===========================================================================
# GET MY PHOTOS
# ===========================================================================

class TestGetMyPhotos:

    async def test_get_photos_empty_for_new_user(self, client, auth_headers):
        resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_photos_requires_auth(self, client):
        resp = await client.get("/api/v1/users/me/photos")
        assert resp.status_code == 401

    async def test_get_photos_returns_all_statuses(self, client, auth_headers, db_session, test_user):
        await upload_and_get_id(client, auth_headers)
        resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photos = resp.json()
        assert len(photos) == 1
        assert photos[0]["status"] == "pending"
        assert photos[0]["reject_reason"] is None
        assert photos[0]["face_verified"] is False

    async def test_get_photos_only_returns_own_photos(
        self, client, auth_headers, second_auth_headers
    ):
        await upload_and_get_id(client, auth_headers)
        resp = await client.get("/api/v1/users/me/photos", headers=second_auth_headers)
        assert resp.json() == []


# ===========================================================================
# DELETE
# ===========================================================================

class TestDeletePhoto:

    async def test_delete_own_photo_succeeds(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.delete(f"/api/v1/users/me/photos/{body['id']}", headers=auth_headers)
        assert resp.status_code == 204

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        assert get_resp.json() == []

    async def test_delete_requires_auth(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.delete(f"/api/v1/users/me/photos/{body['id']}")
        assert resp.status_code == 401

    async def test_delete_nonexistent_photo_404s(self, client, auth_headers):
        fake_id = uuid.uuid4()
        resp = await client.delete(f"/api/v1/users/me/photos/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_cannot_delete_another_users_photo(self, client, auth_headers, second_auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.delete(
            f"/api/v1/users/me/photos/{body['id']}", headers=second_auth_headers
        )
        assert resp.status_code == 404  # not 403 — don't leak existence to non-owners

        # Confirm it's untouched
        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        assert len(get_resp.json()) == 1

    async def test_deleting_main_photo_promotes_next_one(self, client, auth_headers):
        first = await upload_and_get_id(client, auth_headers)
        await upload_and_get_id(client, auth_headers)

        await client.delete(f"/api/v1/users/me/photos/{first['id']}", headers=auth_headers)

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        remaining = get_resp.json()
        assert len(remaining) == 1
        assert remaining[0]["is_main"] is True

    async def test_delete_actually_removes_object_from_storage(self, client, auth_headers):
        """Real MinIO check: after delete, the signed URL should no longer work."""
        body = await upload_and_get_id(client, auth_headers)
        old_url = body["url"]

        await client.delete(f"/api/v1/users/me/photos/{body['id']}", headers=auth_headers)

        async with httpx.AsyncClient() as raw_client:
            img_resp = await raw_client.get(old_url)
        assert img_resp.status_code != 200, "Object should be gone from MinIO after delete"


# ===========================================================================
# SET MAIN PHOTO
# ===========================================================================

class TestSetMainPhoto:

    async def test_cannot_set_pending_photo_as_main(self, client, auth_headers):
        first = await upload_and_get_id(client, auth_headers)
        second = await upload_and_get_id(client, auth_headers)

        # second is pending (not approved) — should be rejected
        resp = await client.put(
            f"/api/v1/users/me/photos/{second['id']}/main", headers=auth_headers
        )
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    async def test_set_main_requires_auth(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.put(f"/api/v1/users/me/photos/{body['id']}/main")
        assert resp.status_code == 401

    async def test_set_main_nonexistent_photo_404s(self, client, auth_headers):
        resp = await client.put(
            f"/api/v1/users/me/photos/{uuid.uuid4()}/main", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_set_approved_photo_as_main_succeeds(self, client, auth_headers):
        first = await upload_and_get_id(client, auth_headers)
        second = await upload_and_get_id(client, auth_headers)

        # Approve the second photo via the admin endpoint
        approve_resp = await client.post(
            f"/api/v1/admin/photos/{second['id']}/approve", headers=ADMIN_HEADERS
        )
        assert approve_resp.status_code == 200, approve_resp.text

        resp = await client.put(
            f"/api/v1/users/me/photos/{second['id']}/main", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["is_main"] is True

        # First photo should no longer be main
        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photos = {p["id"]: p for p in get_resp.json()}
        assert photos[first["id"]]["is_main"] is False


# ===========================================================================
# ADMIN: AUTH
# ===========================================================================

class TestAdminAuth:

    async def test_admin_endpoints_require_admin_key(self, client):
        resp = await client.get("/api/v1/admin/photos/pending")
        assert resp.status_code == 403

    async def test_admin_endpoints_reject_wrong_key(self, client):
        resp = await client.get(
            "/api/v1/admin/photos/pending", headers={"X-Admin-Key": "wrong-key"}
        )
        assert resp.status_code == 403

    async def test_user_bearer_token_does_not_grant_admin_access(self, client, auth_headers):
        """A regular user's JWT should NOT work on admin routes — admin auth
        is header-based (X-Admin-Key), not JWT-based, per deps.py."""
        resp = await client.get("/api/v1/admin/photos/pending", headers=auth_headers)
        assert resp.status_code == 403


# ===========================================================================
# ADMIN: PENDING QUEUE
# ===========================================================================

class TestAdminPendingQueue:

    async def test_pending_queue_empty_initially(self, client):
        resp = await client.get("/api/v1/admin/photos/pending", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_pending_queue_lists_uploaded_photo_with_resolved_name(
        self, client, auth_headers, test_user
    ):
        await upload_and_get_id(client, auth_headers)
        resp = await client.get("/api/v1/admin/photos/pending", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        photos = resp.json()
        assert len(photos) == 1
        # Regression check: user_name must come from UserProfile.name,
        # not a nonexistent current_user.profile.name attribute.
        assert photos[0]["user_name"] == "Test User"
        assert photos[0]["user_email"] == test_user.email
        assert photos[0]["status"] == "pending"

    async def test_pending_queue_excludes_approved_and_rejected(self, client, auth_headers):
        a = await upload_and_get_id(client, auth_headers)
        b = await upload_and_get_id(client, auth_headers)
        await client.post(f"/api/v1/admin/photos/{a['id']}/approve", headers=ADMIN_HEADERS)
        await client.post(
            f"/api/v1/admin/photos/{b['id']}/reject",
            params={"reason": "inappropriate"},
            headers=ADMIN_HEADERS,
        )

        resp = await client.get("/api/v1/admin/photos/pending", headers=ADMIN_HEADERS)
        assert resp.json() == []

    async def test_pending_photo_url_in_admin_queue_is_fetchable(self, client, auth_headers):
        """Real MinIO check on the admin side too."""
        await upload_and_get_id(client, auth_headers)
        resp = await client.get("/api/v1/admin/photos/pending", headers=ADMIN_HEADERS)
        url = resp.json()[0]["url"]

        async with httpx.AsyncClient() as raw_client:
            img_resp = await raw_client.get(url)
        assert img_resp.status_code == 200


# ===========================================================================
# ADMIN: APPROVE
# ===========================================================================

class TestAdminApprove:

    async def test_approve_changes_status(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.post(
            f"/api/v1/admin/photos/{body['id']}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photo = get_resp.json()[0]
        assert photo["status"] == "approved"

    async def test_approve_nonexistent_photo_404s(self, client):
        resp = await client.post(
            f"/api/v1/admin/photos/{uuid.uuid4()}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 404

    async def test_cannot_approve_already_approved_photo(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        await client.post(f"/api/v1/admin/photos/{body['id']}/approve", headers=ADMIN_HEADERS)
        resp = await client.post(
            f"/api/v1/admin/photos/{body['id']}/approve", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 400

    async def test_approve_requires_admin_key(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.post(f"/api/v1/admin/photos/{body['id']}/approve")
        assert resp.status_code == 403

    async def test_approved_photo_moves_to_real_public_url_no_signature(
        self, client, auth_headers
    ):
        """The core real-MinIO regression test: after approval, the photo's
        URL must be a PLAIN public URL (no AWS signature query params) and
        must be fetchable by a totally fresh client with no auth at all —
        proving the bucket policy (not just bucket *name*) actually grants
        anonymous reads on photos-public-test."""
        body = await upload_and_get_id(client, auth_headers)
        old_signed_url = body["url"]
        assert "X-Amz-Signature" in old_signed_url or "Signature" in old_signed_url

        await client.post(f"/api/v1/admin/photos/{body['id']}/approve", headers=ADMIN_HEADERS)

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        new_url = get_resp.json()[0]["url"]
        assert "Signature" not in new_url, (
            "Approved photo should resolve to a plain public URL, not a signed one"
        )
        assert settings.S3_PUBLIC_BUCKET in new_url

        # Fetch with a bare client — no cookies, no auth, nothing.
        async with httpx.AsyncClient() as anon_client:
            img_resp = await anon_client.get(new_url)
        assert img_resp.status_code == 200, (
            f"Public bucket did not actually grant anonymous read access "
            f"(got {img_resp.status_code}) — check that minio_test_init ran "
            f"`mc anonymous set download` on {settings.S3_PUBLIC_BUCKET}"
        )
        assert img_resp.headers["content-type"].startswith("image/")

        # Old private signed URL should now be dead (object moved buckets)
        async with httpx.AsyncClient() as anon_client:
            old_resp = await anon_client.get(old_signed_url)
        assert old_resp.status_code != 200


# ===========================================================================
# ADMIN: REJECT
# ===========================================================================

class TestAdminReject:

    async def test_reject_sets_status_and_reason(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.post(
            f"/api/v1/admin/photos/{body['id']}/reject",
            params={"reason": "Face not clearly visible"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        photo = get_resp.json()[0]
        assert photo["status"] == "rejected"
        assert photo["reject_reason"] == "Face not clearly visible"

    async def test_user_can_still_view_own_rejected_photo(self, client, auth_headers):
        """Per product requirement: users must be able to see their own
        rejected photo (not just the reason) so they understand what to fix."""
        body = await upload_and_get_id(client, auth_headers)
        await client.post(
            f"/api/v1/admin/photos/{body['id']}/reject",
            params={"reason": "blurry"},
            headers=ADMIN_HEADERS,
        )

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        url = get_resp.json()[0]["url"]

        async with httpx.AsyncClient() as raw_client:
            img_resp = await raw_client.get(url)
        assert img_resp.status_code == 200, "Owner should still be able to view rejected photo"

    async def test_rejected_photo_not_publicly_fetchable_without_signature(
        self, client, auth_headers
    ):
        body = await upload_and_get_id(client, auth_headers)
        await client.post(
            f"/api/v1/admin/photos/{body['id']}/reject",
            params={"reason": "blurry"},
            headers=ADMIN_HEADERS,
        )

        # Strip query params (the signature) and try the bare object URL —
        # should NOT be fetchable, since rejected photos stay private.
        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        signed_url = get_resp.json()[0]["url"]
        bare_url = signed_url.split("?")[0]

        async with httpx.AsyncClient() as raw_client:
            img_resp = await raw_client.get(bare_url)
        assert img_resp.status_code != 200, "Rejected photo must NOT be publicly readable"

    async def test_cannot_reject_already_rejected_photo(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        await client.post(
            f"/api/v1/admin/photos/{body['id']}/reject",
            params={"reason": "first reason"},
            headers=ADMIN_HEADERS,
        )
        resp = await client.post(
            f"/api/v1/admin/photos/{body['id']}/reject",
            params={"reason": "second reason"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 400

    async def test_reject_nonexistent_photo_404s(self, client):
        resp = await client.post(
            f"/api/v1/admin/photos/{uuid.uuid4()}/reject",
            params={"reason": "n/a"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404


# ===========================================================================
# ADMIN: STATS
# ===========================================================================

class TestAdminStats:

    async def test_stats_all_zero_initially(self, client):
        resp = await client.get("/api/v1/admin/photos/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"pending": 0, "approved": 0, "rejected": 0, "total": 0}

    async def test_stats_reflect_mixed_statuses(self, client, auth_headers):
        a = await upload_and_get_id(client, auth_headers)
        b = await upload_and_get_id(client, auth_headers)
        c = await upload_and_get_id(client, auth_headers)

        await client.post(f"/api/v1/admin/photos/{a['id']}/approve", headers=ADMIN_HEADERS)
        await client.post(
            f"/api/v1/admin/photos/{b['id']}/reject",
            params={"reason": "x"},
            headers=ADMIN_HEADERS,
        )
        # c stays pending

        resp = await client.get("/api/v1/admin/photos/stats", headers=ADMIN_HEADERS)
        body = resp.json()
        assert body == {"pending": 1, "approved": 1, "rejected": 1, "total": 3}


# ===========================================================================
# ADMIN: GET SINGLE PHOTO / USER PHOTOS
# ===========================================================================

class TestAdminGetPhoto:

    async def test_get_photo_detail_with_resolved_name(self, client, auth_headers, test_user):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.get(f"/api/v1/admin/photos/{body['id']}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["user_name"] == "Test User"
        assert detail["user_email"] == test_user.email

    async def test_get_photo_detail_404_for_missing(self, client):
        resp = await client.get(f"/api/v1/admin/photos/{uuid.uuid4()}", headers=ADMIN_HEADERS)
        assert resp.status_code == 404

    async def test_get_user_photos_for_admin(self, client, auth_headers, test_user):
        await upload_and_get_id(client, auth_headers)
        await upload_and_get_id(client, auth_headers)
        resp = await client.get(
            f"/api/v1/admin/photos/users/{test_user.id}/photos", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_user_photos_404_for_missing_user(self, client):
        resp = await client.get(
            f"/api/v1/admin/photos/users/{uuid.uuid4()}/photos", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 404


# ===========================================================================
# ADMIN: VERIFY FACE
# ===========================================================================

class TestAdminVerifyFace:

    async def test_verify_face_sets_flag(self, client, auth_headers):
        body = await upload_and_get_id(client, auth_headers)
        resp = await client.post(
            f"/api/v1/admin/photos/{body['id']}/verify-face", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["face_verified"] is True

        get_resp = await client.get("/api/v1/users/me/photos", headers=auth_headers)
        assert get_resp.json()[0]["face_verified"] is True

    async def test_verify_face_nonexistent_404s(self, client):
        resp = await client.post(
            f"/api/v1/admin/photos/{uuid.uuid4()}/verify-face", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 404