import pytest
from httpx import AsyncClient
from uuid import UUID
from datetime import datetime

from app.core.config import settings

# ============ URL Constants ============
REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
LIKED_URL = "/api/v1/swipes/liked"
LIKERS_URL = "/api/v1/swipes/likers"
LOGIN_URL = "/api/v1/auth/login"

VALID_CODE = "123456"

MALE_PROFILE = {
    "name": "Liked Male",
    "birth_date": "1999-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}

FEMALE_PROFILE = {
    "name": "Liked Female",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}

FEMALE_PROFILE_2 = {
    "name": "Liked Female 2",
    "birth_date": "1998-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}

FEMALE_PROFILE_3 = {
    "name": "Liked Female 3",
    "birth_date": "1997-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(
    client: AsyncClient,
    mock_verification_code,
    email: str,
    profile: dict,
    password: str = "strongpass123",
) -> dict:
    """Register a user via 3-step flow and return tokens."""
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200

    await mock_verification_code(email, VALID_CODE)

    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email,
        "code": VALID_CODE,
        "password": password,
    })
    assert res.status_code == 200
    tokens = res.json()

    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=profile,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 200
    return res.json()


async def register_male(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "liked_male@example.com", MALE_PROFILE
    )


async def register_female(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "liked_female@example.com", FEMALE_PROFILE
    )


async def register_female_2(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "liked_female2@example.com", FEMALE_PROFILE_2
    )


async def register_female_3(client, mock_verification_code) -> dict:
    return await register_user(
        client, mock_verification_code, "liked_female3@example.com", FEMALE_PROFILE_3
    )


# =============================================================================
# GET /api/v1/swipes/liked
# =============================================================================

class TestSwipeLiked:

    async def test_liked_users_empty(self, client, mock_verification_code):
        """Should return empty list when user has no likes."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.get(LIKED_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["users"] == []
        assert data["total"] == 0
        assert data["next_offset"] is None

    async def test_liked_users_after_like(self, client, mock_verification_code):
        """Should return liked user after swiping right."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}

        # Male likes female
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        res = await client.get(LIKED_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["users"]) == 1
        
        liked_user = data["users"][0]
        assert liked_user["id"] == female["user"]["id"]
        assert liked_user["name"] == FEMALE_PROFILE["name"]
        assert liked_user["age"] == 26  # 2026 - 2000
        assert "swiped_at" in liked_user
        # Verify swiped_at is valid ISO format
        datetime.fromisoformat(liked_user["swiped_at"].replace("Z", "+00:00"))

    async def test_liked_users_pagination(self, client, mock_verification_code):
        """Should respect limit and offset parameters."""
        male = await register_male(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        female3 = await register_female_3(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}

        # Like both females
        await client.post(
            SWIPE_URL,
            json={"user_id": female2["user"]["id"], "direction": "like"},
            headers=male_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": female3["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        # Test limit=1
        res = await client.get(f"{LIKED_URL}?limit=1", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2
        assert len(data["users"]) == 1
        assert data["next_offset"] == 1

        # Test offset=1
        res = await client.get(f"{LIKED_URL}?limit=1&offset=1", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data["users"]) == 1
        assert data["next_offset"] is None

    async def test_liked_users_excludes_blocked(self, client, mock_verification_code, db_session):
        """Should not include blocked users in liked list."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        # Male likes both females
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": female2["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        # Male blocks first female
        from app.models.block import Block
        
        block = Block(blocker_id=male["user"]["id"], blocked_id=female["user"]["id"])
        db_session.add(block)
        await db_session.commit()

        # Liked list should exclude blocked user
        res = await client.get(LIKED_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["users"]) == 1
        assert data["users"][0]["id"] == female2["user"]["id"]

    async def test_liked_users_excludes_inactive(self, client, mock_verification_code, db_session):
        """Should not include inactive users in liked list."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}

        # Male likes female
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        # Deactivate female
        from app.models.user import User
        from sqlalchemy import update
        
        await db_session.execute(
            update(User).where(User.id == female["user"]["id"]).values(is_active=False)
        )
        await db_session.commit()

        # Liked list should exclude inactive user
        res = await client.get(LIKED_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["users"] == []

    async def test_liked_users_requires_auth(self, client):
        """Should return 401 without authentication."""
        res = await client.get(LIKED_URL)
        assert res.status_code == 401

    async def test_liked_users_response_shape(self, client, mock_verification_code):
        """Should return all required fields with correct types."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        res = await client.get(LIKED_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        
        liked_user = data["users"][0]
        
        # Verify all fields present
        assert "id" in liked_user
        assert "name" in liked_user
        assert "age" in liked_user
        assert "main_photo_url" in liked_user
        assert "is_premium" in liked_user
        assert "is_verified" in liked_user
        assert "swiped_at" in liked_user
        
        # Verify types
        UUID(liked_user["id"])  # valid UUID
        assert isinstance(liked_user["name"], str)
        assert isinstance(liked_user["age"], int)
        assert liked_user["main_photo_url"] is None or isinstance(liked_user["main_photo_url"], str)
        assert isinstance(liked_user["is_premium"], bool)
        assert isinstance(liked_user["is_verified"], bool)
        assert isinstance(liked_user["swiped_at"], str)
        
        # Verify swiped_at is valid ISO datetime
        datetime.fromisoformat(liked_user["swiped_at"].replace("Z", "+00:00"))

    async def test_liked_users_sorted_by_most_recent(self, client, mock_verification_code):
        """Should return users sorted by swiped_at DESC (most recent first)."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}

        # Like female first
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )
        
        # Small delay to ensure different timestamps
        import asyncio
        await asyncio.sleep(0.1)
        
        # Like female2 second
        await client.post(
            SWIPE_URL,
            json={"user_id": female2["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        res = await client.get(LIKED_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        
        # Most recent (female2) should be first
        assert data["users"][0]["id"] == female2["user"]["id"]
        assert data["users"][1]["id"] == female["user"]["id"]


# =============================================================================
# GET /api/v1/swipes/likers
# =============================================================================

class TestSwipeLikers:

    async def test_likers_empty(self, client, mock_verification_code):
        """Should return empty list when no one has liked you."""
        male = await register_male(client, mock_verification_code)
        headers = {"Authorization": f"Bearer {male['access_token']}"}

        res = await client.get(LIKERS_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["users"] == []
        assert data["total"] == 0
        assert data["next_offset"] is None

    async def test_likers_after_being_liked(self, client, mock_verification_code):
        """Should return user who liked you."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        # Female likes male
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )

        res = await client.get(LIKERS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["users"]) == 1
        
        liker = data["users"][0]
        assert liker["id"] == female["user"]["id"]
        assert liker["name"] == FEMALE_PROFILE["name"]
        assert liker["age"] == 26
        assert "swiped_at" in liker

    async def test_likers_excludes_matched(self, client, mock_verification_code):
        """Should not include users already matched with."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        # Female likes male
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )
        
        # Male likes female back (creates match)
        await client.post(
            SWIPE_URL,
            json={"user_id": female["user"]["id"], "direction": "like"},
            headers=male_headers,
        )

        # Male's likers list should be empty (female is now a match)
        res = await client.get(LIKERS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["users"] == []

    async def test_likers_pagination(self, client, mock_verification_code):
        """Should respect limit and offset for likers."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        female3 = await register_female_3(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}
        female2_headers = {"Authorization": f"Bearer {female2['access_token']}"}
        female3_headers = {"Authorization": f"Bearer {female3['access_token']}"}

        # All three females like male
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female2_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female3_headers,
        )

        # Test limit=2
        res = await client.get(f"{LIKERS_URL}?limit=2", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["users"]) == 2
        assert data["next_offset"] == 2

        # Test offset=2
        res = await client.get(f"{LIKERS_URL}?limit=2&offset=2", headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data["users"]) == 1
        assert data["next_offset"] is None

    async def test_likers_excludes_blocked(self, client, mock_verification_code, db_session):
        """Should not include blocked users in likers list."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}
        female2_headers = {"Authorization": f"Bearer {female2['access_token']}"}

        # Both females like male
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female2_headers,
        )

        # Male blocks first female
        from app.models.block import Block
        
        block = Block(blocker_id=male["user"]["id"], blocked_id=female["user"]["id"])
        db_session.add(block)
        await db_session.commit()

        # Likers list should exclude blocked user
        res = await client.get(LIKERS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["users"]) == 1
        assert data["users"][0]["id"] == female2["user"]["id"]

    async def test_likers_requires_auth(self, client):
        """Should return 401 without authentication."""
        res = await client.get(LIKERS_URL)
        assert res.status_code == 401

    async def test_likers_response_shape(self, client, mock_verification_code):
        """Should return all required fields with correct types."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )

        res = await client.get(LIKERS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        
        liker = data["users"][0]
        
        # Verify all fields present
        assert "id" in liker
        assert "name" in liker
        assert "age" in liker
        assert "main_photo_url" in liker
        assert "is_premium" in liker
        assert "is_verified" in liker
        assert "swiped_at" in liker
        
        # Verify types
        UUID(liker["id"])
        assert isinstance(liker["name"], str)
        assert isinstance(liker["age"], int)
        assert liker["main_photo_url"] is None or isinstance(liker["main_photo_url"], str)
        assert isinstance(liker["is_premium"], bool)
        assert isinstance(liker["is_verified"], bool)
        assert isinstance(liker["swiped_at"], str)
        
        datetime.fromisoformat(liker["swiped_at"].replace("Z", "+00:00"))

    async def test_likers_sorted_by_most_recent(self, client, mock_verification_code):
        """Should return likers sorted by swiped_at DESC (most recent first)."""
        male = await register_male(client, mock_verification_code)
        female = await register_female(client, mock_verification_code)
        female2 = await register_female_2(client, mock_verification_code)
        male_headers = {"Authorization": f"Bearer {male['access_token']}"}
        female_headers = {"Authorization": f"Bearer {female['access_token']}"}
        female2_headers = {"Authorization": f"Bearer {female2['access_token']}"}

        # Female likes male first
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female_headers,
        )
        
        import asyncio
        await asyncio.sleep(0.1)
        
        # Female2 likes male second
        await client.post(
            SWIPE_URL,
            json={"user_id": male["user"]["id"], "direction": "like"},
            headers=female2_headers,
        )

        res = await client.get(LIKERS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        
        # Most recent (female2) should be first
        assert data["users"][0]["id"] == female2["user"]["id"]
        assert data["users"][1]["id"] == female["user"]["id"]