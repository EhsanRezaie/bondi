# tests/test_search.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SEARCH_URL = "/api/v1/search"
BLOCKS_URL = "/api/v1/blocks"
LOCATION_URL = "/api/v1/users/me/location"

VALID_EMAIL_MALE = "search_male@example.com"
VALID_EMAIL_FEMALE = "search_female@example.com"
VALID_EMAIL_FEMALE2 = "search_female2@example.com"
VALID_EMAIL_FEMALE3 = "search_female3@example.com"
VALID_EMAIL_MALE2 = "search_male2@example.com"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD_MALE = {
    "name": "Search Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "I am a test male user",
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
    "name": "Search Female",
    "birth_date": "1998-05-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "I am a test female user",
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
    "name": "Search Female 2",
    "birth_date": "1995-10-20",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "bisexual",
    "bio": "Another test female user",
    "height": 170,
    "weight": 65,
    "body_type": "curvy",
    "relationship_status": "divorced",
    "living_situation": "alone",
    "children_status": "have",
    "smoking": "occasionally",
    "drinking": "regularly",
    "education": "phd",
    "workplace": "University",
    "religion": "christian",
    "ethnicity": "kurdish",
    "political_orientation": "liberal",
    "languages": ["persian", "english", "german", "arabic"],
    "country": "Iran",
    "province": "Isfahan",
    "city": "Isfahan",
}

COMPLETE_PROFILE_PAYLOAD_FEMALE3 = {
    "name": "Search Female 3",
    "birth_date": "2002-03-10",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Young test female user",
    "height": 160,
    "weight": 55,
    "body_type": "slim",
    "relationship_status": "single",
    "living_situation": "with_family",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "never",
    "education": "high_school",
    "workplace": "Student",
    "religion": "islam",
    "ethnicity": "persian",
    "political_orientation": "moderate",
    "languages": ["persian", "english"],
    "country": "Iran",
    "province": "Fars",
    "city": "Shiraz",
}

COMPLETE_PROFILE_PAYLOAD_MALE2 = {
    "name": "Search Male 2",
    "birth_date": "1997-07-25",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Second male user",
    "height": 185,
    "weight": 80,
    "body_type": "muscular",
    "relationship_status": "single",
    "living_situation": "alone",
    "children_status": "dont_have",
    "smoking": "never",
    "drinking": "socially",
    "education": "bachelor",
    "workplace": "Engineer",
    "religion": "atheist",
    "ethnicity": "persian",
    "political_orientation": "liberal",
    "languages": ["persian", "english", "spanish"],
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


class TestSearch:
    """Test search functionality with all filters."""
    
    async def test_search_returns_users(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return users list."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(SEARCH_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)

    async def test_search_age_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by age range."""
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
            SEARCH_URL,
            params={"age_min": 20, "age_max": 25},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert 20 <= user["age"] <= 25

    async def test_search_gender_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by gender."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"gender": "female"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["gender"] == "female"

    async def test_search_height_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by height range."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"height_min": 160, "height_max": 175},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert 160 <= user["height"] <= 175

    async def test_search_height_greater_than(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by height >= 180."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"height_min": 180},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["height"] >= 180

    async def test_search_weight_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by weight range."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"weight_min": 55, "weight_max": 65},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert 55 <= user["weight"] <= 65

    async def test_search_weight_greater_than(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by weight >= 70."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"weight_min": 70},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["weight"] >= 70

    async def test_search_country_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by country."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"country": "Iran"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["country"] == "Iran"

    async def test_search_province_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by province."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"province": "Isfahan"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["province"] == "Isfahan"

    async def test_search_city_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by city."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE3, COMPLETE_PROFILE_PAYLOAD_FEMALE3, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"city": "Shiraz"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["city"] == "Shiraz"

    async def test_search_religion_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by religion."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"religion": "christian"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["religion"] == "christian"

    async def test_search_ethnicity_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by ethnicity."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"ethnicity": "kurdish"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["ethnicity"] == "kurdish"

    async def test_search_relationship_status_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by relationship status."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"relationship_status": "divorced"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["relationship_status"] == "divorced"

    async def test_search_body_type_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by body type."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"body_type": "curvy"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["body_type"] == "curvy"

    async def test_search_education_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by education level."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"education": "phd"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["education"] == "phd"

    async def test_search_smoking_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by smoking status."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"smoking": "occasionally"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["smoking"] == "occasionally"

    async def test_search_drinking_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by drinking status."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"drinking": "regularly"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["drinking"] == "regularly"

    async def test_search_political_orientation_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by political orientation."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"political_orientation": "liberal"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["political_orientation"] == "liberal"

    async def test_search_languages_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by languages (single language)."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"languages": "french"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            languages = user.get("languages", [])
            assert "french" in languages

    async def test_search_languages_multiple_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by multiple languages (AND condition)."""
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
            SEARCH_URL,
            params={"languages": "persian,english"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            languages = user.get("languages", [])
            assert "persian" in languages
            assert "english" in languages

    async def test_search_interests_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by single interest."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Create female user with "pop music" interest
        female_payload_with_music = {
            **COMPLETE_PROFILE_PAYLOAD_FEMALE,
            "interests": ["pop music", "football"]
        }
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, female_payload_with_music, mock_verification_code
        )
        
        # Create another female user with "traveling" interest
        female_payload_with_travel = {
            **COMPLETE_PROFILE_PAYLOAD_FEMALE2,
            "interests": ["traveling", "painting"]
        }
        _, female2_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, female_payload_with_travel, mock_verification_code
        )
        
        # Search by "pop music" interest
        res = await client.get(
            SEARCH_URL,
            params={"interests": "pop music"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        user_ids = [u["id"] for u in data["users"]]
        assert female_id in user_ids
        assert female2_id not in user_ids

    async def test_search_interests_multiple_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by multiple interests (AND condition)."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Create female user with "pop music" and "football" interests
        female_payload_with_both = {
            **COMPLETE_PROFILE_PAYLOAD_FEMALE,
            "interests": ["pop music", "football", "traveling"]
        }
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, female_payload_with_both, mock_verification_code
        )
        
        # Create another female user with only "pop music"
        female_payload_with_music_only = {
            **COMPLETE_PROFILE_PAYLOAD_FEMALE2,
            "interests": ["pop music", "painting"]
        }
        _, female2_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, female_payload_with_music_only, mock_verification_code
        )
        
        # Search by "pop music" AND "football" (must have both)
        res = await client.get(
            SEARCH_URL,
            params={"interests": "pop music,football"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        user_ids = [u["id"] for u in data["users"]]
        assert female_id in user_ids
        assert female2_id not in user_ids

    async def test_search_distance_filter(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by distance."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Set current user's location
        await client.post(
            LOCATION_URL,
            params={"lat": 35.6892, "lng": 51.3890},
            headers=male_headers
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"distance_km": 100},
            headers=male_headers,
        )
        assert res.status_code == 200

    async def test_search_sort_by_age(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should sort results by age."""
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
            SEARCH_URL,
            params={"sort_by": "age", "sort_order": "asc"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        ages = [user["age"] for user in data["users"]]
        assert ages == sorted(ages)

    async def test_search_sort_by_name(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should sort results by name."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={"sort_by": "name", "sort_order": "asc"},
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        names = [user["name"] for user in data["users"]]
        assert names == sorted(names)

    async def test_search_pagination(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should support pagination."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE3, COMPLETE_PROFILE_PAYLOAD_FEMALE3, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_MALE2, COMPLETE_PROFILE_PAYLOAD_MALE2, mock_verification_code
        )
        
        # First page
        res1 = await client.get(
            SEARCH_URL,
            params={"limit": 2, "offset": 0},
            headers=male_headers,
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert len(data1["users"]) <= 2
        
        # Second page
        res2 = await client.get(
            SEARCH_URL,
            params={"limit": 2, "offset": 2},
            headers=male_headers,
        )
        assert res2.status_code == 200
        data2 = res2.json()
        
        # Different results
        ids1 = [u["id"] for u in data1["users"]]
        ids2 = [u["id"] for u in data2["users"]]
        assert len(set(ids1).intersection(set(ids2))) == 0

    async def test_search_combined_filters(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should combine multiple filters."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        # Female, age 25-30, height 160-175
        res = await client.get(
            SEARCH_URL,
            params={
                "gender": "female",
                "age_min": 25,
                "age_max": 30,
                "height_min": 160,
                "height_max": 175,
            },
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["gender"] == "female"
            assert 25 <= user["age"] <= 30
            assert 160 <= user["height"] <= 175

    async def test_search_combined_location_filters(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by country, province, and city together."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE3, COMPLETE_PROFILE_PAYLOAD_FEMALE3, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={
                "country": "Iran",
                "province": "Fars",
                "city": "Shiraz",
            },
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["country"] == "Iran"
            assert user["province"] == "Fars"
            assert user["city"] == "Shiraz"

    async def test_search_lifestyle_filters(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should filter by lifestyle preferences."""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        res = await client.get(
            SEARCH_URL,
            params={
                "smoking": "occasionally",
                "drinking": "regularly",
                "children_status": "have",
            },
            headers=male_headers,
        )
        assert res.status_code == 200
        data = res.json()
        
        for user in data["users"]:
            assert user["smoking"] == "occasionally"
            assert user["drinking"] == "regularly"
            assert user["children_status"] == "have"



    async def test_search_excludes_users_who_blocked_you(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Users who blocked you should NOT appear in search results."""
        # Create user A
        user_a_headers, user_a_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Create user B (will block user A)
        user_b_headers, user_b_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Search from user A's perspective - should see user B
        search_res_before = await client.get(SEARCH_URL, headers=user_a_headers)
        users_before = search_res_before.json().get("users", [])
        user_ids_before = [u["id"] for u in users_before]
        assert user_b_id in user_ids_before
        
        # User B blocks User A
        await client.post(f"{BLOCKS_URL}/{user_a_id}/block", headers=user_b_headers)
        
        # Search from user A's perspective - should NOT see user B
        search_res_after = await client.get(SEARCH_URL, headers=user_a_headers)
        users_after = search_res_after.json().get("users", [])
        user_ids_after = [u["id"] for u in users_after]
        
        assert user_b_id not in user_ids_after

    async def test_search_excludes_both_block_directions(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Both users who you blocked and users who blocked you are excluded."""
        # Create user A
        user_a_headers, user_a_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Create user B (will block User A)
        user_b_headers, user_b_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Create user C (will be blocked by User A)
        user_c_headers, user_c_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        # User B blocks User A
        await client.post(f"{BLOCKS_URL}/{user_a_id}/block", headers=user_b_headers)
        
        # User A blocks User C
        await client.post(f"{BLOCKS_URL}/{user_c_id}/block", headers=user_a_headers)
        
        # Search from user A's perspective
        search_res = await client.get(SEARCH_URL, headers=user_a_headers)
        users = search_res.json().get("users", [])
        user_ids = [u["id"] for u in users]
        
        assert user_b_id not in user_ids  # User B blocked you
        assert user_c_id not in user_ids  # You blocked User C

    async def test_search_requires_auth(self, client: AsyncClient):
        """Should return 401 without token."""
        res = await client.get(SEARCH_URL)
        assert res.status_code == 401


class TestBlocks:
    """Test block functionality (included with search)."""
    
    async def test_block_user_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should block a user."""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=headers)
        assert res.status_code == 204
    
    async def test_block_self_fails(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot block yourself."""
        headers, user_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.post(f"{BLOCKS_URL}/{user_id}/block", headers=headers)
        assert res.status_code == 400
    
    async def test_unblock_user_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should unblock a user."""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=headers)
        res = await client.post(f"{BLOCKS_URL}/{female_id}/unblock", headers=headers)
        assert res.status_code == 204
    
    async def test_list_blocks(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should list blocked users."""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=headers)
        
        res = await client.get(BLOCKS_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
    
    async def test_blocked_user_not_in_search(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Blocked user should not appear in search results."""
        # Create blocker
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        # Create user to block
        _, block_user_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Search before block - should see the user
        search_res_before = await client.get(SEARCH_URL, headers=headers)
        users_before = search_res_before.json().get("users", [])
        user_ids_before = [u["id"] for u in users_before]
        assert block_user_id in user_ids_before
        
        # Block the user
        await client.post(f"{BLOCKS_URL}/{block_user_id}/block", headers=headers)
        
        # Search after block - should NOT see the user
        search_res_after = await client.get(SEARCH_URL, headers=headers)
        users_after = search_res_after.json().get("users", [])
        user_ids_after = [u["id"] for u in users_after]
        
        assert block_user_id not in user_ids_after


class TestSearchSwipeStatus:
    """Test current_user_action field in search results."""

    async def test_search_returns_null_for_unswiped_user(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should return current_user_action: null for users not yet swiped."""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )

        res = await client.get(SEARCH_URL, headers=male_headers)
        assert res.status_code == 200

        users = res.json()["users"]
        female_user = next(u for u in users if u["id"] == female_id)
        assert female_user["current_user_action"] is None

    async def test_search_returns_like_after_liking(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should return current_user_action: 'like' after liking a user."""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )

        # Like the female user
        await client.post(
            "/api/v1/swipes",
            json={"user_id": female_id, "direction": "like"},
            headers=male_headers,
        )

        res = await client.get(SEARCH_URL, headers=male_headers)
        users = res.json()["users"]
        female_user = next(u for u in users if u["id"] == female_id)
        assert female_user["current_user_action"] == "like"

    async def test_search_returns_pass_after_passing(
        self, client: AsyncClient, mock_verification_code
    ):
        """Should return current_user_action: 'pass' after passing on a user."""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        _, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )

        # Pass on the female user
        await client.post(
            "/api/v1/swipes",
            json={"user_id": female_id, "direction": "pass"},
            headers=male_headers,
        )

        res = await client.get(SEARCH_URL, headers=male_headers)
        users = res.json()["users"]
        female_user = next(u for u in users if u["id"] == female_id)
        assert female_user["current_user_action"] == "pass"