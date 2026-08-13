import pytest
from httpx import AsyncClient

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
TICKETS_URL = "/api/v1/tickets"

VALID_EMAIL = "ticket_user@example.com"
VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Ticket User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(
    client: AsyncClient,
    email: str = VALID_EMAIL,
    mock_verification_code=None,
) -> dict:
    # Step 1: Init
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text

    # Step 2: Store verification code
    if mock_verification_code:
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
        json=COMPLETE_PROFILE,
        headers=headers,
    )
    assert res.status_code == 200, res.text

    return res.json()


class TestUserTickets:
    """Test user ticket submission and viewing"""

    async def test_create_ticket_success(self, client: AsyncClient, mock_verification_code):
        """User should be able to create a support ticket"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={
                "subject": "Help with account",
                "message": "I need help with my account settings. Please assist."
            },
            headers=headers
        )
        assert res.status_code == 201
        body = res.json()
        assert body["subject"] == "Help with account"
        assert body["message"] == "I need help with my account settings. Please assist."
        assert body["status"] == "open"
        assert "id" in body

    async def test_create_ticket_subject_too_short(self, client: AsyncClient, mock_verification_code):
        """Should reject subject shorter than 3 characters"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={"subject": "Hi", "message": "This is a test message"},
            headers=headers
        )
        assert res.status_code == 422

    async def test_create_ticket_subject_too_long(self, client: AsyncClient, mock_verification_code):
        """Should reject subject longer than 200 characters"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={"subject": "a" * 201, "message": "Test message"},
            headers=headers
        )
        assert res.status_code == 422

    async def test_create_ticket_message_too_short(self, client: AsyncClient, mock_verification_code):
        """Should reject message shorter than 10 characters"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={"subject": "Help", "message": "Too short"},
            headers=headers
        )
        assert res.status_code == 422

    async def test_create_ticket_message_too_long(self, client: AsyncClient, mock_verification_code):
        """Should reject message longer than 2000 characters"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={"subject": "Help", "message": "a" * 2001},
            headers=headers
        )
        assert res.status_code == 422

    async def test_create_ticket_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication"""
        res = await client.post(
            TICKETS_URL,
            json={"subject": "Help", "message": "Need assistance"}
        )
        assert res.status_code == 401

    async def test_get_my_tickets_empty(self, client: AsyncClient, mock_verification_code):
        """Should return empty list when user has no tickets"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(TICKETS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["tickets"] == []
        assert body["total"] == 0

    async def test_get_my_tickets_with_data(self, client: AsyncClient, mock_verification_code):
        """Should return user's tickets"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        # Create multiple tickets
        for i in range(3):
            await client.post(
                TICKETS_URL,
                json={"subject": f"Ticket {i}", "message": f"Message {i}" * 10},
                headers=headers
            )

        res = await client.get(TICKETS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["tickets"]) == 3
        assert body["total"] == 3

    async def test_get_my_tickets_pagination(self, client: AsyncClient, mock_verification_code):
        """Should paginate tickets correctly"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        for i in range(5):
            await client.post(
                TICKETS_URL,
                json={"subject": f"Ticket {i}", "message": f"Message {i}" * 10},
                headers=headers
            )

        res = await client.get(TICKETS_URL, params={"limit": 2}, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["tickets"]) == 2
        assert body["total"] == 5
        assert body["next_offset"] == 2

    async def test_get_ticket_detail_success(self, client: AsyncClient, mock_verification_code):
        """Should return ticket details for user's own ticket"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "My Ticket", "message": "This is my ticket message"},
            headers=headers
        )
        ticket_id = create_res.json()["id"]

        res = await client.get(f"{TICKETS_URL}/{ticket_id}", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == ticket_id
        assert body["subject"] == "My Ticket"
        assert body["message"] == "This is my ticket message"

    async def test_get_ticket_detail_not_found(self, client: AsyncClient, mock_verification_code):
        """Should return 404 for non-existent ticket"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(
            f"{TICKETS_URL}/00000000-0000-0000-0000-000000000001",
            headers=headers
        )
        assert res.status_code == 404

    async def test_get_ticket_detail_other_user(self, client: AsyncClient, mock_verification_code):
        """Should not allow viewing other user's ticket"""
        # Create user A with ticket
        userA_data = await register_user(
            client, VALID_EMAIL, mock_verification_code=mock_verification_code
        )
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "User A Ticket", "message": "This is user A's ticket"},
            headers=userA_headers
        )
        ticket_id = create_res.json()["id"]

        # Create user B trying to view user A's ticket
        userB_data = await register_user(
            client, "userb@example.com", mock_verification_code=mock_verification_code
        )
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        res = await client.get(f"{TICKETS_URL}/{ticket_id}", headers=userB_headers)
        assert res.status_code == 404

    async def test_get_ticket_detail_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication"""
        res = await client.get(f"{TICKETS_URL}/00000000-0000-0000-0000-000000000001")
        assert res.status_code == 401


class TestUserTicketReplies:
    """Test user replying to their own ticket (conversation)"""

    async def test_create_ticket_seeds_initial_message(self, client: AsyncClient, mock_verification_code):
        """Creating a ticket should seed the initial message in the thread"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            TICKETS_URL,
            json={"subject": "Thread Test", "message": "Initial message body here"},
            headers=headers
        )
        assert res.status_code == 201
        body = res.json()
        assert len(body["messages"]) == 1
        assert body["messages"][0]["sender_type"] == "user"
        assert body["messages"][0]["content"] == "Initial message body here"

    async def test_reply_to_ticket_success(self, client: AsyncClient, mock_verification_code):
        """User should be able to reply to their own open ticket"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "Reply Test", "message": "Please help me"},
            headers=headers
        )
        ticket_id = create_res.json()["id"]

        res = await client.post(
            f"{TICKETS_URL}/{ticket_id}/messages",
            json={"content": "Adding more details now"},
            headers=headers
        )
        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "open"
        assert len(body["messages"]) == 2
        assert body["messages"][0]["sender_type"] == "user"
        assert body["messages"][1]["sender_type"] == "user"
        assert body["messages"][1]["content"] == "Adding more details now"

    async def test_reply_to_closed_ticket_reopens(self, client: AsyncClient, mock_verification_code):
        """Replying to a closed ticket should reopen it to 'open'"""
        from app.core.config import settings

        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "Reopen Test", "message": "Ticket to be closed"},
            headers=headers
        )
        ticket_id = create_res.json()["id"]

        # Admin closes the ticket
        admin_headers = {"X-Admin-Key": settings.ADMIN_SECRET_KEY}
        await client.patch(
            f"/api/v1/admin/tickets/{ticket_id}",
            json={"status": "closed"},
            headers=admin_headers
        )

        # User replies → reopened
        res = await client.post(
            f"{TICKETS_URL}/{ticket_id}/messages",
            json={"content": "This should reopen the ticket"},
            headers=headers
        )
        assert res.status_code == 201
        assert res.json()["status"] == "open"

    async def test_reply_to_other_users_ticket(self, client: AsyncClient, mock_verification_code):
        """Should not allow replying to another user's ticket (IDOR)"""
        userA_data = await register_user(
            client, VALID_EMAIL, mock_verification_code=mock_verification_code
        )
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "User A Ticket", "message": "This is user A's ticket"},
            headers=userA_headers
        )
        ticket_id = create_res.json()["id"]

        userB_data = await register_user(
            client, "userb_reply@example.com", mock_verification_code=mock_verification_code
        )
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        res = await client.post(
            f"{TICKETS_URL}/{ticket_id}/messages",
            json={"content": "Intrusion attempt"},
            headers=userB_headers
        )
        assert res.status_code == 404

    async def test_reply_to_nonexistent_ticket(self, client: AsyncClient, mock_verification_code):
        """Should return 404 for reply to non-existent ticket"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            f"{TICKETS_URL}/00000000-0000-0000-0000-000000000001/messages",
            json={"content": "No such ticket"},
            headers=headers
        )
        assert res.status_code == 404

    async def test_reply_message_too_short(self, client: AsyncClient, mock_verification_code):
        """Should reject reply shorter than 10 characters"""
        data = await register_user(client, mock_verification_code=mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        create_res = await client.post(
            TICKETS_URL,
            json={"subject": "Short Reply", "message": "Full message here"},
            headers=headers
        )
        ticket_id = create_res.json()["id"]

        res = await client.post(
            f"{TICKETS_URL}/{ticket_id}/messages",
            json={"content": "short"},
            headers=headers
        )
        assert res.status_code == 422

    async def test_reply_requires_auth(self, client: AsyncClient):
        """Should return 401 without authentication"""
        res = await client.post(
            f"{TICKETS_URL}/00000000-0000-0000-0000-000000000001/messages",
            json={"content": "No auth message"}
        )
        assert res.status_code == 401