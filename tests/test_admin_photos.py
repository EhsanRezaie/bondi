
import pytest
import numpy as np
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image
import io
from unittest.mock import patch, AsyncMock
from app.core.config import settings
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


@pytest_asyncio.fixture(autouse=True)
async def _mock_face_check():
    """Mock face embedding extraction so photo uploads never load InsightFace."""
    with patch(
        "app.api.v1.endpoints.photos.face_verification_service.extract_single_photo_embedding",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = (np.random.randn(512).astype(np.float32), "")
        yield m


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
ADMIN_PHOTOS_URL = "/api/v1/admin/photos"
ADMIN_KEY = settings.ADMIN_SECRET_KEY
VALID_CODE = "123456"
COMPLETE_PROFILE = {
    "name": "Test User",
    "birth_date": "1995-06-15",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, phone: str = _phone("photo@example.com"), mock_verification_code=None) -> dict:
    """Helper: complete full registration via phone OTP flow."""
    if mock_verification_code:
        await mock_verification_code(phone, VALID_CODE)

    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


class TestAdminPhotos:
    """Test admin photo moderation"""

    async def create_test_photo(self, client, headers):
        """Helper to create a test photo. Color varies per call and the image is
        random noise seeded from that color, so consecutive uploads are NOT
        flagged as duplicates by the dHash check."""
        n = getattr(self, "_n", 0)
        self._n = n + 1
        color = (30 + n * 41 % 225, 80 + n * 53 % 175, 130 + n * 29 % 125)
        rng = np.random.default_rng(
            (color[0] << 16) | (color[1] << 8) | color[2]
        )
        img = Image.fromarray(
            rng.integers(0, 256, size=(200, 200, 3), dtype=np.uint8),
            mode="RGB",
        )
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)

        files = {"file": ("test.jpg", img_bytes, "image/jpeg")}

        return await client.post(
            "/api/v1/users/me/photos",
            files=files,
            headers=headers
        )

    async def test_admin_get_pending_photos(self, client: AsyncClient, mock_verification_code):
        """Admin should list pending photos"""
        # Upload a photo (status = pending)
        user_data = await register_user(client, _phone("photopending@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        await self.create_test_photo(client, user_headers)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/pending", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 1

    async def test_admin_approve_photo(self, client: AsyncClient, mock_verification_code):
        """Admin should approve a pending photo"""
        # Upload a photo
        user_data = await register_user(client, _phone("photoapprove@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(
            f"{ADMIN_PHOTOS_URL}/{photo_id}/approve",
            headers=admin_headers
        )
        assert res.status_code == 200
        assert res.json()["message"] == "Photo approved successfully"

    async def test_admin_reject_photo(self, client: AsyncClient, mock_verification_code):
        """Admin should reject a pending photo with reason"""
        # Upload a photo
        user_data = await register_user(client, _phone("photoreject@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(
            f"{ADMIN_PHOTOS_URL}/{photo_id}/reject",
            params={"reason": "Inappropriate content"},
            headers=admin_headers
        )
        assert res.status_code == 200
        assert res.json()["message"] == "Photo rejected successfully"
        assert res.json()["reason"] == "Inappropriate content"

    async def test_admin_photo_stats(self, client: AsyncClient):
        """Admin should get photo moderation statistics"""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/stats", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert "pending" in body
        assert "approved" in body
        assert "rejected" in body
        assert "total" in body

    async def test_admin_get_photo_detail(self, client: AsyncClient, mock_verification_code):
        """Admin should get photo details with user info"""
        # Upload a photo
        user_data = await register_user(client, _phone("photodetail@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/{photo_id}", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert body["id"] == photo_id
        assert "user_id" in body
        assert "user_name" in body
        assert "url" in body
        assert "status" in body

    async def test_admin_verify_face(self, client: AsyncClient, mock_verification_code):
        """Admin should mark photo as face-verified"""
        # Upload a photo
        user_data = await register_user(client, _phone("faceverify@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(
            f"{ADMIN_PHOTOS_URL}/{photo_id}/verify-face",
            headers=admin_headers
        )
        assert res.status_code == 200
        assert res.json()["face_verified"] is True

    async def test_admin_get_user_photos(self, client: AsyncClient, mock_verification_code):
        """Admin should get all photos for a specific user"""
        # Upload photos
        user_data = await register_user(client, _phone("userphotos@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        await self.create_test_photo(client, user_headers)
        await self.create_test_photo(client, user_headers)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(
            f"{ADMIN_PHOTOS_URL}/users/{user_data['user']['id']}/photos",
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 2

    async def test_pending_photos_response_shape(self, client: AsyncClient, mock_verification_code):
        """AdminPendingPhotoResponse should contain all schema fields."""
        user_data = await register_user(client, _phone("pendingshape@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        await self.create_test_photo(client, user_headers)

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/pending", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 1

        photo = body[0]
        assert isinstance(photo["id"], str)
        assert isinstance(photo["user_id"], str)
        assert isinstance(photo["user_name"], str)
        assert "user_email" in photo  # nullable for phone-based users
        assert isinstance(photo["url"], str)
        assert isinstance(photo["is_main"], bool)
        assert photo["status"] == "pending"
        assert isinstance(photo["face_verified"], bool)
        assert isinstance(photo["created_at"], str)

    async def test_approve_response_shape(self, client: AsyncClient, mock_verification_code):
        """AdminPhotoActionResponse should match schema."""
        user_data = await register_user(client, _phone("approveshape@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(f"{ADMIN_PHOTOS_URL}/{photo_id}/approve", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert body["message"] == "Photo approved successfully"
        assert body["photo_id"] == photo_id

    async def test_reject_response_shape(self, client: AsyncClient, mock_verification_code):
        """AdminPhotoRejectResponse should match schema."""
        user_data = await register_user(client, _phone("rejectshape@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(
            f"{ADMIN_PHOTOS_URL}/{photo_id}/reject",
            params={"reason": "Inappropriate"},
            headers=admin_headers
        )
        assert res.status_code == 200
        body = res.json()

        assert body["message"] == "Photo rejected successfully"
        assert body["photo_id"] == photo_id
        assert body["reason"] == "Inappropriate"

    async def test_stats_response_shape(self, client: AsyncClient):
        """AdminPhotoStatsResponse should contain all 4 fields."""
        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/stats", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert isinstance(body["pending"], int)
        assert isinstance(body["approved"], int)
        assert isinstance(body["rejected"], int)
        assert isinstance(body["total"], int)
        assert body["total"] == body["pending"] + body["approved"] + body["rejected"]

    async def test_verify_face_response_shape(self, client: AsyncClient, mock_verification_code):
        """AdminPhotoVerifyResponse should match schema."""
        user_data = await register_user(client, _phone("faceverify2@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.post(f"{ADMIN_PHOTOS_URL}/{photo_id}/verify-face", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert body["message"] == "Photo verified"
        assert body["photo_id"] == photo_id
        assert body["face_verified"] is True

    async def test_photo_detail_contains_user_email(self, client: AsyncClient, mock_verification_code):
        """AdminPhotoDetailResponse should include user_email."""
        user_data = await register_user(client, _phone("detailemail@example.com"), mock_verification_code)
        user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
        upload_res = await self.create_test_photo(client, user_headers)
        photo_id = upload_res.json()["id"]

        admin_headers = {"X-Admin-Key": ADMIN_KEY}
        res = await client.get(f"{ADMIN_PHOTOS_URL}/{photo_id}", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()

        assert "user_email" in body
        assert body["user_email"] is None  # phone-based auth; email is optional

    async def test_admin_photos_requires_admin_key(self, client: AsyncClient, mock_verification_code):
        """Should return 401 with wrong admin key"""
        # Create a normal user (not admin)
        user_data = await register_user(client, _phone("photoauth@example.com"), mock_verification_code)

        # Try to access admin endpoint with valid JWT + WRONG admin key
        wrong_headers = {
            "X-Admin-Key": "WRONG_KEY_123"
        }
        res = await client.get(f"{ADMIN_PHOTOS_URL}/pending", headers=wrong_headers)
        assert res.status_code == 403
        assert "Admin access required" in res.json()["detail"]
