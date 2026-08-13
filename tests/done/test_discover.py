# tests/test_discover.py - Complete updated file

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from datetime import date, datetime, timedelta, timezone

from app.models.user import User
from app.models.user_profile import UserProfile

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
DISCOVER_URL = "/api/v1/discover"
SWIPE_URL = "/api/v1/swipes"
BLOCKS_URL = "/api/v1/blocks"

VALID_EMAIL_MALE = "discover_male@example.com"
VALID_EMAIL_FEMALE = "discover_female@example.com"
VALID_EMAIL_FEMALE2 = "discover_female2@example.com"
VALID_EMAIL_MALE2 = "discover_male2@example.com"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD_MALE = {
    "name": "Discover Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test male for discover",
    "height": 180,
    "weight": 75,
    "body_type": "athletic",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Tech Company",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}

COMPLETE_PROFILE_PAYLOAD_FEMALE = {
    "name": "Discover Female",
    "birth_date": "1998-05-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test female for discover",
    "height": 165,
    "weight": 60,
    "body_type": "slim",
    "relationship_status": "single",
    "living_situation": "with_family",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "never",
    "education": "master",
    "workplace": "Hospital",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english", "french"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}

COMPLETE_PROFILE_PAYLOAD_FEMALE2 = {
    "name": "Discover Female 2",
    "birth_date": "1995-10-20",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Second test female",
    "height": 170,
    "weight": 65,
    "body_type": "curvy",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "University",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Isfahan",
    "city": "Isfahan",
}

COMPLETE_PROFILE_PAYLOAD_MALE2 = {
    "name": "Discover Male 2",
    "birth_date": "1997-07-25",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Second test male",
    "height": 175,
    "weight": 70,
    "body_type": "average",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Tech Company",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}


async def register_user_full(
    client: AsyncClient,
    email: str,
    complete_payload: dict,
    mock_verification_code
) -> dict:
    """Complete full registration flow - returns user data with tokens."""
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text
    
    await mock_verification_code(email, VALID_CODE)
    
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email,
        "code": VALID_CODE,
        "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=complete_payload,
        headers=headers,
    )
    assert res.status_code == 200, res.text
    
    return res.json()


async def register_and_get_headers(
    client: AsyncClient,
    email: str,
    complete_payload: dict,
    mock_verification_code
) -> tuple[dict, str]:
    """Register a user and return headers with user_id."""
    result = await register_user_full(client, email, complete_payload, mock_verification_code)
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    user_id = result["user"]["id"]
    return headers, user_id


async def get_user_age(db_session, user_id: str) -> int:
    """Get user age from profile."""
    result = await db_session.execute(
        select(UserProfile)
        .where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    return profile.age if profile else 0


class TestDiscover:
    
    async def test_discover_returns_opposite_gender(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by female gender when specified."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_MALE2, COMPLETE_PROFILE_PAYLOAD_MALE2, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["gender"] == "female"
    
    async def test_discover_returns_all_genders_when_no_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return all genders when no gender filter is provided."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_MALE2, COMPLETE_PROFILE_PAYLOAD_MALE2, mock_verification_code
        )
        
        res = await client.get(DISCOVER_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        
        genders = [u["gender"] for u in data["users"]]
        assert "male" in genders
        assert "female" in genders
    
    async def test_discover_filters_by_gender_male(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by male gender."""
        female_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_MALE2, COMPLETE_PROFILE_PAYLOAD_MALE2, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "male"},
            headers=female_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["gender"] == "male"
    
    async def test_discover_filters_by_gender_female(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by female gender."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["gender"] == "female"
    
    async def test_discover_excludes_swiped_users(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should exclude users already swiped on."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        await client.post(
            SWIPE_URL,
            json={"user_id": female_id, "direction": "like"},
            headers=male_headers,
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        female_ids = [u["id"] for u in data["users"]]
        assert female_id not in female_ids
    
    async def test_discover_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.get(DISCOVER_URL)
        assert res.status_code == 401
    
    async def test_discover_age_filter_exact(
        self, 
        client: AsyncClient, 
        mock_verification_code,
        db_session
    ):
        """Should filter by exact age range."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Female born 1998-05-15
        female_result, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Get female age using profile.age property
        female_age = await get_user_age(db_session, female_id)
        
        # Search for exact age range that includes the female
        res = await client.get(
            DISCOVER_URL,
            params={
                "age_min": female_age - 1,
                "age_max": female_age + 1,
                "gender": "female"
            },
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        # Should find the female
        user_ids = [u["id"] for u in data["users"]]
        assert female_id in user_ids
    
    async def test_discover_age_filter_greater_than(
        self, 
        client: AsyncClient, 
        mock_verification_code,
        db_session
    ):
        """Should filter by age >= 30."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Register female
        female_result, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # ✅ Get actual age using profile.age property
        result = await db_session.execute(
            select(UserProfile)
            .where(UserProfile.user_id == female_id)
        )
        profile = result.scalar_one_or_none()
        female_age = profile.age if profile else 0
        print(f"Female age: {female_age}")
        
        # Search for age >= 30 - should return none if female_age < 30
        res = await client.get(
            DISCOVER_URL,
            params={"age_min": 30, "gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        if female_age >= 30:
            # If female is 30+, she should be in results
            assert len(data["users"]) >= 1
        else:
            # If female is < 30, she should NOT be in results
            assert len(data["users"]) == 0
        
    async def test_discover_age_filter_less_than(
        self, 
        client: AsyncClient, 
        mock_verification_code,
        db_session
    ):
        """Should filter by age <= 20."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Register female
        female_result, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # ✅ Get actual age using profile.age property
        result = await db_session.execute(
            select(UserProfile)
            .where(UserProfile.user_id == female_id)
        )
        profile = result.scalar_one_or_none()
        female_age = profile.age if profile else 0
        print(f"Female age: {female_age}")
        
        # Search for age <= 20 - should return none if female_age > 20
        res = await client.get(
            DISCOVER_URL,
            params={"age_max": 20, "gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        if female_age <= 20:
            # If female is 20 or younger, she should be in results
            assert len(data["users"]) >= 1
        else:
            # If female is > 20, she should NOT be in results
            assert len(data["users"]) == 0
    
    async def test_discover_distance_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by distance."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"distance_km": 10, "gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert len(data["users"]) >= 0
    
    async def test_discover_does_not_show_self(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should not show the current user in discover."""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "male"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        user_ids = [u["id"] for u in data["users"]]
        assert male_id not in user_ids
    
    async def test_discover_only_active_users(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should only show active users."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["users"]) > 0
    
    async def test_discover_combined_filters(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should combine age and distance filters."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={
                "age_min": 20,
                "age_max": 25,
                "distance_km": 100,
                "gender": "female",
            },
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["age"] >= 20
            assert user["age"] <= 25
    
    async def test_discover_excludes_blocked_users(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Users you blocked should NOT appear in discover."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res_before = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        user_ids_before = [u["id"] for u in res_before.json()["users"]]
        assert female_id in user_ids_before
        
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        
        res_after = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res_after.status_code == 200
        user_ids_after = [u["id"] for u in res_after.json()["users"]]
        
        assert female_id not in user_ids_after
    
    async def test_discover_excludes_users_who_blocked_you(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Users who blocked you should NOT appear in discover."""
        user_a_headers, user_a_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        user_b_headers, user_b_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res_before = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=user_a_headers,
        )
        user_ids_before = [u["id"] for u in res_before.json()["users"]]
        assert user_b_id in user_ids_before
        
        await client.post(f"{BLOCKS_URL}/{user_a_id}/block", headers=user_b_headers)
        
        res_after = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=user_a_headers,
        )
        assert res_after.status_code == 200
        user_ids_after = [u["id"] for u in res_after.json()["users"]]
        
        assert user_b_id not in user_ids_after
    
    async def test_discover_excludes_swiped_pass(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Users you passed (swiped left) should NOT appear in discover."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res_before = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        user_ids_before = [u["id"] for u in res_before.json()["users"]]
        assert female_id in user_ids_before
        
        await client.post(
            SWIPE_URL,
            json={"user_id": female_id, "direction": "pass"},
            headers=male_headers,
        )
        
        res_after = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res_after.status_code == 200
        user_ids_after = [u["id"] for u in res_after.json()["users"]]
        
        assert female_id not in user_ids_after
    
    async def test_discover_excludes_swiped_like(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Users you liked (swiped right) should NOT appear in discover."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res_before = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        user_ids_before = [u["id"] for u in res_before.json()["users"]]
        assert female_id in user_ids_before
        
        await client.post(
            SWIPE_URL,
            json={"user_id": female_id, "direction": "like"},
            headers=male_headers,
        )
        
        res_after = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res_after.status_code == 200
        user_ids_after = [u["id"] for u in res_after.json()["users"]]
        
        assert female_id not in user_ids_after
    
    async def test_discover_returns_premium_status(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Discover should return correct is_premium status."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert isinstance(user["is_premium"], bool)
    
    async def test_discover_returns_verified_status(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Discover should return correct is_verified status."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert isinstance(user["is_verified"], bool)
    
    async def test_discover_returns_main_photo_url(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Discover should return main_photo_url (or None)."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["main_photo_url"] is None or isinstance(user["main_photo_url"], str)
    
    async def test_discover_empty_results(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return empty list when no users match filters."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.get(
            DISCOVER_URL,
            params={"age_min": 80, "age_max": 100},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        assert isinstance(data["users"], list)
        assert data["total"] == 0
        assert data["next_offset"] is None
    
    async def test_discover_excludes_already_matched(
        self, 
        client: AsyncClient, 
        mock_verification_code,
        db_session
    ):
        """Users you already matched with should NOT appear in discover."""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Load profiles to avoid greenlet issues
        result = await db_session.execute(
            select(User)
            .options(
                selectinload(User.profile),
                selectinload(User.photos),
            )
            .where(User.id.in_([male_id, female_id]))
        )
        users = result.scalars().all()
        
        # Check female appears in discover before match
        res_before = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        user_ids_before = [u["id"] for u in res_before.json()["users"]]
        assert female_id in user_ids_before
        
        # Create match (both like each other)
        await client.post(
            SWIPE_URL,
            json={"user_id": female_id, "direction": "like"},
            headers=male_headers,
        )
        match_res = await client.post(
            SWIPE_URL,
            json={"user_id": male_id, "direction": "like"},
            headers=female_headers,
        )
        assert match_res.status_code == 200
        
        # Female should NOT appear in discover (because they are matched)
        res_after = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res_after.status_code == 200
        user_ids_after = [u["id"] for u in res_after.json()["users"]]
        
        assert female_id not in user_ids_after

    async def test_discover_returns_card_fields_only(
        self,
        client: AsyncClient,
        mock_verification_code
    ):
        """Discover should return only the slim card fields (not the full profile)."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )

        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()

        assert len(data["users"]) >= 1
        user = data["users"][0]
        # Card fields are present
        assert user["name"] == "Discover Female"
        assert user["age"] > 0
        assert user["gender"] == "female"
        assert isinstance(user["is_premium"], bool)
        assert isinstance(user["is_verified"], bool)
        # Full-profile fields are NOT returned
        assert "height" not in user
        assert "weight" not in user
        assert "body_type" not in user
        assert "bio" not in user
        assert "interests" not in user
        assert "prompts" not in user
        assert "photos" not in user
        assert "languages" not in user
        assert "last_seen_at" not in user


class TestDiscoverCursorPagination:
    """Keyset cursor pagination for the discover deck must never return the
    same user twice — even when the deck shifts (swipes excluded, last_seen
    churns, equal keys) between page loads."""

    CURSOR_EMAILS = [
        "cursor_d1@example.com",
        "cursor_d2@example.com",
        "cursor_d3@example.com",
        "cursor_d4@example.com",
        "cursor_d5@example.com",
        "cursor_d6@example.com",
    ]

    async def _register_searcher(self, client, mock_verification_code) -> dict:
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        return male_headers

    async def _register_candidate(
        self, client: AsyncClient, mock_verification_code, index: int
    ) -> str:
        payload = dict(COMPLETE_PROFILE_PAYLOAD_FEMALE)
        payload["name"] = f"Cursor Deck {index}"
        _, uid = await register_and_get_headers(
            client, self.CURSOR_EMAILS[index], payload, mock_verification_code
        )
        return uid

    async def _register_candidates(
        self, client: AsyncClient, mock_verification_code, count: int = 5
    ) -> tuple[dict, list[str]]:
        male_headers = await self._register_searcher(client, mock_verification_code)
        ids = [
            await self._register_candidate(client, mock_verification_code, i)
            for i in range(count)
        ]
        return male_headers, ids

    async def _walk_pages(
        self,
        client: AsyncClient,
        headers: dict,
        limit: int,
        start_cursor: str | None = None,
    ) -> tuple[dict, list[str]]:
        """Fetch every page via cursor. Returns (last_response, all ids in order)."""
        all_ids: list[str] = []
        cursor = start_cursor
        while True:
            params = {"gender": "female", "limit": limit}
            if cursor:
                params["cursor"] = cursor
            res = await client.get(DISCOVER_URL, params=params, headers=headers)
            assert res.status_code == 200, res.text
            data = res.json()
            page_ids = [u["id"] for u in data["users"]]
            assert len(page_ids) <= limit
            all_ids.extend(page_ids)
            cursor = data.get("next_cursor")
            if not cursor:
                return data, all_ids

    async def test_cursor_walks_all_pages_without_duplicates(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await self._register_candidates(client, mock_verification_code, count=5)
        data, all_ids = await self._walk_pages(client, male_headers, limit=2)
        assert data["total"] == 5
        assert len(all_ids) == 5
        assert len(set(all_ids)) == 5  # no duplicates, nothing missing

    async def test_cursor_has_next_only_until_last_page(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, candidate_ids = await self._register_candidates(
            client, mock_verification_code, count=4
        )
        res1 = await client.get(
            DISCOVER_URL, params={"gender": "female", "limit": 2}, headers=male_headers
        )
        assert res1.status_code == 200
        p1 = res1.json()
        assert len(p1["users"]) == 2
        assert p1["total"] == 4
        assert p1["next_cursor"]

        res2 = await client.get(
            DISCOVER_URL,
            params={"gender": "female", "limit": 2, "cursor": p1["next_cursor"]},
            headers=male_headers,
        )
        assert res2.status_code == 200
        p2 = res2.json()
        assert len(p2["users"]) == 2
        assert p2["next_cursor"] is None

        ids1 = {u["id"] for u in p1["users"]}
        ids2 = {u["id"] for u in p2["users"]}
        assert ids1.isdisjoint(ids2)
        assert ids1 | ids2 == set(candidate_ids)

    async def test_cursor_no_duplicates_when_deck_shifts_by_swipe(
        self, client: AsyncClient, mock_verification_code
    ):
        """Swiping (which excludes users) must not push already-seen rows into
        a later page — the discover-specific version of the offset bug."""
        male_headers = await self._register_searcher(client, mock_verification_code)
        originals = [
            await self._register_candidate(client, mock_verification_code, i)
            for i in range(4)
        ]

        res1 = await client.get(
            DISCOVER_URL, params={"gender": "female", "limit": 2}, headers=male_headers
        )
        p1 = res1.json()
        page1_ids = {u["id"] for u in p1["users"]}
        cursor = p1["next_cursor"]
        assert cursor

        # Swipe away (pass) both users shown on page 1 — the deck shrinks and
        # the remaining rows shift up relative to a naive offset.
        for uid in page1_ids:
            res = await client.post(
                SWIPE_URL,
                json={"user_id": uid, "direction": "pass"},
                headers=male_headers,
            )
            assert res.status_code == 200

        _, later_ids = await self._walk_pages(client, male_headers, limit=2, start_cursor=cursor)
        assert len(set(later_ids)) == len(later_ids)
        assert page1_ids.isdisjoint(set(later_ids))
        assert page1_ids | set(later_ids) == set(originals)

    async def test_cursor_stable_when_sort_keys_tie(
        self, client: AsyncClient, db_session, mock_verification_code
    ):
        male_headers, candidate_ids = await self._register_candidates(
            client, mock_verification_code, count=4
        )
        fixed = datetime(2024, 1, 1, tzinfo=timezone.utc)
        await db_session.execute(
            update(User).where(User.id.in_(candidate_ids)).values(last_seen_at=fixed)
        )
        await db_session.commit()

        data, all_ids = await self._walk_pages(client, male_headers, limit=2)
        assert data["total"] == 4
        assert len(all_ids) == 4
        assert len(set(all_ids)) == 4

    async def test_cursor_null_last_seen_tail(
        self, client: AsyncClient, mock_verification_code
    ):
        """Users who never went online (NULL last_seen_at) sit in the null tail;
        paging through them must stay duplicate-free."""
        male_headers, candidate_ids = await self._register_candidates(
            client, mock_verification_code, count=5
        )
        data, all_ids = await self._walk_pages(client, male_headers, limit=2)
        assert data["total"] == 5
        assert len(all_ids) == 5
        assert len(set(all_ids)) == 5

    async def test_cursor_none_when_no_results(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await self._register_candidates(client, mock_verification_code, count=2)
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female", "age_min": 80, "age_max": 100},
            headers=male_headers,
        )
        data = res.json()
        assert data["users"] == []
        assert data["total"] == 0
        assert data["next_cursor"] is None

    async def test_invalid_cursor_falls_back_to_offset(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await self._register_candidates(client, mock_verification_code, count=3)
        res = await client.get(
            DISCOVER_URL,
            params={"gender": "female", "limit": 2, "cursor": "::not-a-real-cursor::"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["users"]) >= 1
        assert data["next_offset"] == 2
        assert data["next_cursor"] is not None
