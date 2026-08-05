import pytest
from httpx import AsyncClient

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
SWIPE_URL = "/api/v1/swipes"
MATCHES_URL = "/api/v1/matches"

VALID_EMAIL_MALE = "match_male@example.com"
VALID_EMAIL_FEMALE = "match_female@example.com"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE_MALE = {
    "name": "Match Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}

COMPLETE_PROFILE_FEMALE = {
    "name": "Match Female",
    "birth_date": "2000-06-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_and_get_headers(
    client: AsyncClient,
    email: str,
    complete_payload: dict,
    mock_verification_code,
) -> tuple[dict, str]:
    # Step 1: Init
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text

    # Step 2: Store verification code
    await mock_verification_code(email, VALID_CODE)

    # Step 3: Verify
    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email,
        "code": VALID_CODE,
        "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()

    # Step 4: Complete profile
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(
        REGISTER_COMPLETE_URL,
        json=complete_payload,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    result = res.json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    user_id = result["user"]["id"]
    return headers, user_id


class TestMatches:

    async def test_get_matches_empty(self, client: AsyncClient, mock_verification_code):
        """Should return empty list when no matches"""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_MALE, mock_verification_code
        )

        res = await client.get(MATCHES_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["matches"] == []
        assert data["total"] == 0

    async def test_get_matches_after_match(self, client: AsyncClient, mock_verification_code):
        """Should return matches after mutual like"""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_FEMALE, mock_verification_code
        )

        # Male likes female
        await client.post(
            SWIPE_URL,
            json={"user_id": female_id, "direction": "like"},
            headers=male_headers,
        )

        # Female likes male (creates match)
        await client.post(
            SWIPE_URL,
            json={"user_id": male_id, "direction": "like"},
            headers=female_headers,
        )

        # Check matches for male
        res = await client.get(MATCHES_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1

        # Check match structure
        match = data["matches"][0]
        assert "id" in match
        assert "matched_at" in match
        assert "user" in match
        assert "id" in match["user"]
        assert "name" in match["user"]
        assert "age" in match["user"]

    async def test_get_match_detail(self, client: AsyncClient, mock_verification_code):
        """Should return match details"""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_FEMALE, mock_verification_code
        )

        # Create match
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
        match_data = match_res.json()
        match_id = match_data["match_id"]

        # Get match detail
        res = await client.get(f"{MATCHES_URL}/{match_id}", headers=male_headers)
        assert res.status_code == 200
        data = res.json()

        assert data["id"] == match_id
        assert "user1" in data
        assert "user2" in data
        assert "matched_at" in data
        assert data["is_active"] == True

    async def test_get_match_detail_unauthorized(self, client: AsyncClient, mock_verification_code):
        """Should not allow access to other user's match"""
        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_FEMALE, mock_verification_code
        )

        # Create match
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
        match_id = match_res.json()["match_id"]

        # Register third user
        third_headers, _ = await register_and_get_headers(
            client,
            "third@example.com",
            {
                "name": "Third User",
                "birth_date": "2000-01-01",
                "gender": "male",
                "lat": 35.6892,
                "lng": 51.3890,
            },
            mock_verification_code,
        )

        # Third user trying to access match
        res = await client.get(f"{MATCHES_URL}/{match_id}", headers=third_headers)
        assert res.status_code == 404

    async def test_get_matches_requires_auth(self, client: AsyncClient):
        """Should return 401 without token"""
        res = await client.get(MATCHES_URL)
        assert res.status_code == 401

class TestMatchPresence:
    """Match list should include online status + last seen."""

    async def test_get_matches_includes_online_status(
        self, client: AsyncClient, mock_verification_code
    ):
        import app.core.redis as redis_module

        male_headers, male_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_FEMALE, mock_verification_code
        )

        await client.post(SWIPE_URL, json={"user_id": female_id, "direction": "like"}, headers=male_headers)
        await client.post(SWIPE_URL, json={"user_id": male_id, "direction": "like"}, headers=female_headers)

        # Mark the female user online in Redis
        await redis_module.redis_client.setex(f"online:{female_id}", 60, "1")

        res = await client.get(MATCHES_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        match = data["matches"][0]
        assert "is_online" in match["user"]
        assert match["user"]["is_online"] is True
        assert "last_seen_at" in match["user"]
