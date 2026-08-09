# tests/test_blocks.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
BLOCKS_URL = "/api/v1/blocks"
SEARCH_URL = "/api/v1/search"
CHATS_URL = "/api/v1/chats"

VALID_EMAIL_MALE = "block_male@example.com"
VALID_EMAIL_FEMALE = "block_female@example.com"
VALID_EMAIL_FEMALE2 = "block_female2@example.com"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE_PAYLOAD_MALE = {
    "name": "Block Male",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 180,
    "weight": 75,
}

COMPLETE_PROFILE_PAYLOAD_FEMALE = {
    "name": "Block Female",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 165,
    "weight": 60,
}

COMPLETE_PROFILE_PAYLOAD_FEMALE2 = {
    "name": "Block Female 2",
    "birth_date": "2000-01-01",
    "gender": "female",
    "lat": 35.6892,
    "lng": 51.3890,
    "sexual_orientation": "straight",
    "bio": "Test bio",
    "height": 170,
    "weight": 65,
}


async def register_user_full(
    client: AsyncClient,
    email: str,
    complete_payload: dict,
    mock_verification_code
) -> dict:
    """Complete full registration flow - returns user data with tokens."""
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


class TestBlocks:
    
    async def test_block_user_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should block a user successfully"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        assert res.status_code == 204
    
    async def test_block_self_fails(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot block yourself"""
        headers, user_id = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.post(f"{BLOCKS_URL}/{user_id}/block", headers=headers)
        assert res.status_code == 400
        assert "Cannot block yourself" in res.json()["detail"]
    
    async def test_block_nonexistent_user_fails(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot block non-existent user"""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.post(
            f"{BLOCKS_URL}/00000000-0000-0000-0000-000000000000/block", 
            headers=headers
        )
        assert res.status_code == 404
        assert "User not found" in res.json()["detail"]
    
    async def test_block_twice_fails(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot block same user twice"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # First block
        res1 = await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        assert res1.status_code == 204
        
        # Second block - should fail
        res2 = await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        assert res2.status_code == 400
        assert "already blocked" in res2.json()["detail"].lower()
    
    async def test_unblock_user_success(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should unblock a user successfully"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Block first
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        
        # Then unblock
        res = await client.post(f"{BLOCKS_URL}/{female_id}/unblock", headers=male_headers)
        assert res.status_code == 204
    
    async def test_unblock_not_blocked_fails(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Cannot unblock a user that wasn't blocked"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        res = await client.post(f"{BLOCKS_URL}/{female_id}/unblock", headers=male_headers)
        assert res.status_code == 404
        assert "Block not found" in res.json()["detail"]
    
    async def test_list_blocks_empty(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return empty list when no blocks"""
        headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        
        res = await client.get(BLOCKS_URL, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) == 0
    
    async def test_list_blocks_with_blocks(
        self, 
        client: AsyncClient, 
        mock_verification_code
    ):
        """Should return list of blocked users"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        female2_headers, female2_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE2, COMPLETE_PROFILE_PAYLOAD_FEMALE2, mock_verification_code
        )
        
        # Block two users
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        await client.post(f"{BLOCKS_URL}/{female2_id}/block", headers=male_headers)
        
        res = await client.get(BLOCKS_URL, headers=male_headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 2
        
        # Check response structure
        for block in data:
            assert "id" in block
            assert "blocked_user_id" in block
            assert "blocked_user_name" in block
            assert "blocked_at" in block
    
    async def test_blocked_user_not_in_search(
        self, 
        client: AsyncClient, 
        mock_verification_code,
        db_session
    ):
        """Blocked users should not appear in search results"""
        male_headers, _ = await register_and_get_headers(
            client, VALID_EMAIL_MALE, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, VALID_EMAIL_FEMALE, COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )
        
        # Load profiles
        result = await db_session.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.id.in_([female_id]))
        )
        users = result.scalars().all()
        
        # Search before block - should see female
        res_before = await client.get(SEARCH_URL, headers=male_headers)
        assert res_before.status_code == 200
        users_before = [u["id"] for u in res_before.json()["users"]]
        
        # Block female
        await client.post(f"{BLOCKS_URL}/{female_id}/block", headers=male_headers)
        
        # Search after block - should NOT see female
        res_after = await client.get(SEARCH_URL, headers=male_headers)
        assert res_after.status_code == 200
        users_after = [u["id"] for u in res_after.json()["users"]]
        
        assert female_id in users_before
        assert female_id not in users_after
    
    async def test_block_requires_auth(self, client: AsyncClient):
        """Block endpoint requires authentication"""
        res = await client.post(f"{BLOCKS_URL}/123/block")
        assert res.status_code == 401
    
    async def test_unblock_requires_auth(self, client: AsyncClient):
        """Unblock endpoint requires authentication"""
        res = await client.post(f"{BLOCKS_URL}/123/unblock")
        assert res.status_code == 401

    async def test_list_blocks_requires_auth(self, client: AsyncClient):
        """List blocks endpoint requires authentication"""
        res = await client.get(BLOCKS_URL)
        assert res.status_code == 401

    async def test_blocked_target_cannot_be_unmatched_chatted(
        self, client: AsyncClient, mock_verification_code
    ):
        """A blocked user must not be able to open an unmatched chat (403)."""
        male_headers, male_id = await register_and_get_headers(
            client, "block_chat_sender@example.com", COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
        )
        female_headers, female_id = await register_and_get_headers(
            client, "block_chat_target@example.com", COMPLETE_PROFILE_PAYLOAD_FEMALE, mock_verification_code
        )

        # Female blocks male (reverse direction).
        await client.post(
            f"{BLOCKS_URL}/{male_id}/block", headers=female_headers
        )

        # Male now tries to message female directly.
        res = await client.post(
            CHATS_URL, json={"user_id": female_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code == 403
        assert "cannot start a chat" in res.json()["detail"].lower()