
# tests/test_public_profile.py - tests for GET /users/{user_id}

import uuid
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


from httpx import AsyncClient

VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
USERS_URL = "/api/v1/users"
BLOCKS_URL = "/api/v1/blocks"

VALID_CODE = "123456"

PROFILE_MALE = {
    "name": "Public Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test male for public profile",
    "height": 180,
    "weight": 75,
    "body_type": "athletic",
    "relationship_status": "single",
    "education": "bachelor",
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}

PROFILE_FEMALE = {
    "name": "Public Female",
    "birth_date": "1998-05-15",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Hello female for public profile",
    "height": 165,
    "weight": 60,
    "body_type": "slim",
    "relationship_status": "single",
    "education": "master",
    "country": "Iran",
    "province": "Tehran",
    "city": "Tehran",
}


async def register_full(client, phone, payload, mock_verification_code):
    """Register a user fully via phone OTP; returns headers dict + user id."""
    await mock_verification_code(phone, VALID_CODE)
    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    data = res.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
    assert res.status_code == 200, res.text
    result = res.json()
    return {"Authorization": f"Bearer {result['access_token']}"}, result["user"]["id"]


class TestPublicProfile:

    async def test_get_public_profile_success(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await register_full(
            client, _phone("pub_male@example.com"), PROFILE_MALE, mock_verification_code
        )
        _, female_id = await register_full(
            client, _phone("pub_female@example.com"), PROFILE_FEMALE, mock_verification_code
        )

        res = await client.get(f"{USERS_URL}/{female_id}", headers=male_headers)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["id"] == female_id
        assert data["name"] == "Public Female"
        assert data["gender"] == "female"
        assert data["age"] > 0
        assert data["bio"] == "Hello female for public profile"
        assert data["education"] == "master"
        assert data["country"] == "Iran"
        assert data["is_premium"] is True
        assert data["photos"] is None or isinstance(data["photos"], list)
        assert "distance_km" in data

    async def test_get_public_profile_returns_404_for_missing(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await register_full(
            client, _phone("pub_male2@example.com"), PROFILE_MALE, mock_verification_code
        )
        res = await client.get(f"{USERS_URL}/{uuid.uuid4()}", headers=male_headers)
        assert res.status_code == 404, res.text

    async def test_get_public_profile_404_when_blocked(
        self, client: AsyncClient, mock_verification_code
    ):
        male_headers, _ = await register_full(
            client, _phone("pub_male3@example.com"), PROFILE_MALE, mock_verification_code
        )
        _, female_id = await register_full(
            client, _phone("pub_female3@example.com"), PROFILE_FEMALE, mock_verification_code
        )

        block_res = await client.post(
            f"{BLOCKS_URL}/{female_id}/block", headers=male_headers
        )
        assert block_res.status_code == 204, block_res.text

        res = await client.get(f"{USERS_URL}/{female_id}", headers=male_headers)
        assert res.status_code == 404, res.text