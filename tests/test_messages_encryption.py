# tests/test_messages_encryption.py
import pytest
import pytest_asyncio
import uuid
from datetime import datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from unittest.mock import patch

from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings
from app.models.chat import Chat
from app.models.message import Message
from app.core.security import create_access_token


pytestmark = pytest.mark.asyncio


# ============================================
# FIXTURES
# ============================================

async def _make_user(db_session: AsyncSession, email: str, name: str, gender: str,
                     referral: str, birth_date: datetime):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        phone=f"+9891{uuid.uuid4().hex[:10]}",
        email=email,
        phone_verified=True,
        is_active=True,
        registration_status="onboarding_complete",
        referral_code=referral,
        created_at=datetime.utcnow(),
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(UserProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        birth_date=birth_date,
        gender=gender,
        bio="Test bio",
        lat=35.6892,
        lng=51.3890,
        country="Iran",
        province="Tehran",
        city="Tehran",
        is_verified=True,
        premium_until=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    db_session.add(UserSettings(
        id=uuid.uuid4(),
        user_id=user.id,
        hide_last_seen=False,
        hide_online_status=False,
        push_enabled=True,
        like_notifications=True,
        match_notifications=True,
        message_notifications=True,
        language="en",
        dark_mode=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))

    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "testuser@example.com", "Test User", "male",
                            "TESTUSER123", datetime(1990, 1, 1).date())


@pytest_asyncio.fixture
async def test_user2(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "testuser2@example.com", "Test User 2", "female",
                            "TESTUSER456", datetime(1992, 5, 15).date())


@pytest_asyncio.fixture
async def test_user3(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "testuser3@example.com", "Test User 3", "male",
                            "TESTUSER789", datetime(1988, 10, 20).date())


@pytest_asyncio.fixture
async def test_chat(db_session: AsyncSession, test_user: User, test_user2: User) -> Chat:
    """Create an accepted chat between test_user and test_user2."""
    a, b = sorted([test_user.id, test_user2.id])
    chat = Chat(
        id=uuid.uuid4(),
        initiator_id=a,
        recipient_id=b,
        status="accepted",
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    return chat


@pytest_asyncio.fixture
async def test_chat2(db_session: AsyncSession, test_user: User, test_user3: User) -> Chat:
    """Create a second accepted chat (test_user ↔ test_user3)."""
    a, b = sorted([test_user.id, test_user3.id])
    chat = Chat(
        id=uuid.uuid4(),
        initiator_id=a,
        recipient_id=b,
        status="accepted",
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(chat)
    await db_session.commit()
    await db_session.refresh(chat)
    return chat


@pytest_asyncio.fixture
def auth_headers(test_user: User) -> dict:
    access_token = create_access_token(user_id=str(test_user.id))
    return {"Authorization": f"Bearer {access_token}"}


@pytest_asyncio.fixture
def admin_headers() -> dict:
    from app.core.config import settings
    return {"X-Admin-Key": settings.ADMIN_SECRET_KEY}


@pytest_asyncio.fixture
def test_image() -> bytes:
    import base64
    return base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMI"
        b"QAAAABJRU5ErkJggg=="
    )


# ============================================
# TESTS
# ============================================

class TestMessageEncryptionAPI:
    """Test message encryption through API endpoints"""

    async def test_send_text_message_encrypted(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "Hello, this is a secret message!"},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()
        assert message is not None
        assert message._content is not None
        assert message._content != "Hello, this is a secret message!"
        assert len(message._content) > 10
        assert message.content == "Hello, this is a secret message!"

    async def test_get_chat_history_decrypts_messages(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        messages = ["First message", "Second message", "Third message with emojis 🎉"]

        for msg in messages:
            response = await client.post(
                f"/api/v1/messages/{test_chat.id}/text",
                json={"content": msg},
                headers=auth_headers
            )
            assert response.status_code == 200

        response = await client.get(
            f"/api/v1/messages/{test_chat.id}", headers=auth_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["messages"]) == len(messages)
        for i, msg_data in enumerate(data["messages"]):
            assert msg_data["content"] == messages[i]

    async def test_message_encrypted_in_db_not_plaintext(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        original_text = "This should not be stored in plaintext"
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": original_text},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()

        assert message._content != original_text
        assert "This" not in message._content
        assert "plaintext" not in message._content
        assert message.content == original_text

    async def test_photo_message_caption_encrypted(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict, test_image: bytes
    ):
        with patch("app.services.media_service.MediaService.save_photo") as mock_save:
            mock_save.return_value = (True, "http://test.com/photo.jpg", None)

            response = await client.post(
                f"/api/v1/messages/{test_chat.id}/photo",
                files={"file": ("test.jpg", test_image, "image/jpeg")},
                data={"caption": "This is a photo caption"},
                headers=auth_headers
            )
            assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()

        assert message._content != "This is a photo caption"
        assert len(message._content) > 10
        assert message.content == "This is a photo caption"

    async def test_message_deletion_encryption(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "Delete me"},
            headers=auth_headers
        )
        assert response.status_code == 200
        message_id = response.json()["id"]

        response = await client.delete(
            f"/api/v1/messages/{message_id}?delete_for=me",
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalar_one_or_none()
        assert message.is_deleted_for_sender == True

    async def test_forward_message_encryption(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, test_chat2: Chat, auth_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "Forward me!"},
            headers=auth_headers
        )
        assert response.status_code == 200
        message_id = response.json()["id"]

        response = await client.post(
            f"/api/v1/messages/{message_id}/forward",
            json={"target_chat_id": str(test_chat2.id)},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat2.id)
        )
        messages = result.scalars().all()

        assert len(messages) >= 1
        forwarded = messages[-1]
        assert forwarded._content != "Forward me!"
        assert len(forwarded._content) > 10
        assert "Forwarded:" in forwarded.content


class TestAdminMessageEncryption:
    """Test admin encryption endpoints"""

    async def test_admin_decrypt_message(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict, admin_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "Admin can read this"},
            headers=auth_headers
        )
        assert response.status_code == 200
        message_id = response.json()["id"]

        response = await client.get(
            f"/api/v1/admin/messages/{message_id}/decrypt",
            headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["content"] == "Admin can read this"
        assert data["message_id"] == message_id
        assert "chat_id" in data

    async def test_admin_decrypt_unauthorized(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "Secret message"},
            headers=auth_headers
        )
        assert response.status_code == 200
        message_id = response.json()["id"]

        response = await client.get(
            f"/api/v1/admin/messages/{message_id}/decrypt",
            headers=auth_headers
        )
        assert response.status_code in [401, 403]

    async def test_admin_delete_message(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict, admin_headers: dict
    ):
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": "This will be deleted by admin"},
            headers=auth_headers
        )
        assert response.status_code == 200
        message_id = response.json()["id"]

        response = await client.delete(
            f"/api/v1/admin/messages/{message_id}",
            headers=admin_headers,
            params={"reason": "Spam"}
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalar_one_or_none()

        assert message.is_deleted_for_all == True
        assert "[Deleted by admin: Spam]" in message._content


class TestEncryptionEdgeCases:
    """Test encryption edge cases"""

    async def test_very_long_message(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        long_message = "A" * 5000
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": long_message},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()

        assert message._content != long_message
        assert message.content == long_message

    async def test_message_with_emojis(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        emoji_message = "Hello world! 🌍👋 How are you? 😊🎉"
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": emoji_message},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()

        assert message.content == emoji_message
        assert message._content != emoji_message

    async def test_message_with_persian_text(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        persian_message = "سلام! حالت چطوره؟ امروز هوا خوبه ☀️"
        response = await client.post(
            f"/api/v1/messages/{test_chat.id}/text",
            json={"content": persian_message},
            headers=auth_headers
        )
        assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(Message.chat_id == test_chat.id)
        )
        message = result.scalar_one_or_none()

        assert message.content == persian_message
        assert message._content != persian_message

    async def test_multiple_messages_same_chat(
        self, client: AsyncClient, db_session: AsyncSession,
        test_user: User, test_chat: Chat, auth_headers: dict
    ):
        messages = ["Message 1", "Message 2", "Message 3"]

        for msg in messages:
            response = await client.post(
                f"/api/v1/messages/{test_chat.id}/text",
                json={"content": msg},
                headers=auth_headers
            )
            assert response.status_code == 200

        result = await db_session.execute(
            select(Message).where(
                Message.chat_id == test_chat.id
            ).order_by(Message.sent_at)
        )
        db_messages = result.scalars().all()

        encrypted_contents = [msg._content for msg in db_messages]
        assert len(set(encrypted_contents)) == len(encrypted_contents)

        for i, msg in enumerate(db_messages):
            assert msg.content == messages[i]