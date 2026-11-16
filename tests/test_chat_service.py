from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

from app.models.block import Block
from app.models.chat import Chat
from app.models.daily_limit import DailyLimit
from app.models.message import Message
from app.models.swipe import Swipe
from app.models.user import User
from app.services import chat_service


def _user():
    return User(
        id=uuid4(),
        phone=f"+9891{uuid4().hex[:10]}",
        phone_verified=True,
        is_active=True,
        token_version=1,
        registration_status="onboarding_complete",
    )


def _chat(initiator, recipient, **kwargs):
    return Chat(
        id=uuid4(),
        initiator_id=initiator.id,
        recipient_id=recipient.id,
        **kwargs,
    )


class TestDailyLimitsAndPairQueries:
    async def test_get_or_create_daily_limit_returns_existing_or_adds_new(self, db_session):
        user = _user()
        existing = DailyLimit(
            user_id=user.id,
            date=date(2026, 1, 1),
            likes_used=2,
            chats_used=1,
            ad_likes_bonus=0,
            ad_chats_bonus=0,
        )
        db_session.add_all([user, existing])
        await db_session.commit()

        got_existing = await chat_service.get_or_create_daily_limit(
            db_session, user.id, date(2026, 1, 1)
        )
        created = await chat_service.get_or_create_daily_limit(
            db_session, user.id, date(2026, 1, 2)
        )

        assert got_existing.id == existing.id
        assert created.likes_used == 0
        assert created.chats_used == 0
        assert created.ad_likes_bonus == 0
        assert created.ad_chats_bonus == 0

    def test_pair_lock_key_is_stable_order_independent_signed_64_bit(self):
        first, second = uuid4(), uuid4()

        key = chat_service.pair_lock_key(first, second)

        assert key == chat_service.pair_lock_key(second, first)
        assert -(2**63) <= key < 2**63

    async def test_find_chat_for_pair_and_last_messages(self, db_session):
        first, second, third = _user(), _user(), _user()
        chat = _chat(first, second, status="accepted")
        inactive = _chat(first, third, status="accepted", is_active=False)
        old_message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            sent_at=datetime.now(UTC) - timedelta(hours=1),
        )
        latest_message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=second.id,
            receiver_id=first.id,
            message_type="text",
            sent_at=datetime.now(UTC),
        )
        hidden_message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            is_deleted_for_all=True,
            sent_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        db_session.add_all([first, second, third, chat, inactive, old_message, latest_message, hidden_message])
        await db_session.commit()

        assert await chat_service.find_chat_for_pair(db_session, second.id, first.id) == chat
        assert await chat_service.find_chat_for_pair(
            db_session, first.id, third.id, only_active=False
        ) == inactive
        assert await chat_service.find_chat_for_pair(db_session, first.id, third.id) is None
        assert await chat_service.get_last_messages_for_chats(db_session, []) == {}
        last = await chat_service.get_last_messages_for_chats(db_session, [chat.id])
        assert last == {chat.id: latest_message}

    async def test_get_user_chat_and_create_chat(self, db_session):
        first, second, unrelated = _user(), _user(), _user()
        chat = _chat(first, second, status="pending")
        db_session.add_all([first, second, unrelated, chat])
        await db_session.commit()

        assert await chat_service.get_user_chat(db_session, chat.id, first.id) == chat
        assert await chat_service.get_user_chat(db_session, chat.id, unrelated.id) is None
        created = await chat_service.create_chat_for_pair(
            db_session, second.id, unrelated.id, "pending"
        )
        assert created.initiator_id == second.id
        assert created.recipient_id == unrelated.id
        assert created.is_active is True


class TestChatRules:
    async def test_chat_is_ended_from_flags_or_block(self, db_session):
        first, second = _user(), _user()
        chat = _chat(first, second)
        db_session.add_all([first, second, chat])
        await db_session.commit()

        assert await chat_service.chat_is_ended(db_session, chat, first.id) is False
        chat.deleted_for_recipient = True
        assert await chat_service.chat_is_ended(db_session, chat, first.id) is True
        chat.deleted_for_recipient = False
        chat.is_ended = True
        assert await chat_service.chat_is_ended(db_session, chat, first.id) is True
        chat.is_ended = False
        db_session.add(Block(blocker_id=first.id, blocked_id=second.id))
        await db_session.flush()
        assert await chat_service.chat_is_ended(db_session, chat, first.id) is True

    async def test_mutual_like_and_chat_start_rules(self, db_session, monkeypatch):
        first, second = _user(), _user()
        db_session.add_all([first, second])
        await db_session.flush()
        db_session.add_all(
            [
                Swipe(from_user=first.id, to_user=second.id, direction="like"),
                Swipe(from_user=second.id, to_user=first.id, direction="like"),
            ]
        )
        await db_session.commit()

        assert await chat_service.have_mutual_like(db_session, first.id, second.id) is True
        assert await chat_service.can_start_new_chat(db_session, uuid4(), True) == (True, None)
        assert await chat_service.can_start_new_chat(db_session, uuid4(), False) == (
            False,
            "User not found",
        )

        async def remaining(_user):
            return 0

        monkeypatch.setattr(chat_service.RewardService, "get_remaining_chats", AsyncMock(side_effect=remaining))
        can_start, reason = await chat_service.can_start_new_chat(db_session, first.id, False)
        assert can_start is False
        assert "Daily new chat limit reached" in reason

        async def remaining_chats(_user):
            return 1

        monkeypatch.setattr(
            chat_service.RewardService,
            "get_remaining_chats",
            AsyncMock(side_effect=remaining_chats),
        )
        assert await chat_service.can_start_new_chat(db_session, first.id, False) == (True, None)

    async def test_consume_new_chat_and_send_message_rules(self, db_session, monkeypatch):
        first, second = _user(), _user()
        chat = _chat(first, second, status="pending")
        db_session.add_all([first, second, chat])
        await db_session.commit()

        consume_chat = AsyncMock(return_value=True)
        monkeypatch.setattr(chat_service.RewardService, "consume_chat", consume_chat)
        assert await chat_service.consume_new_chat(db_session, first.id, True) is True
        consume_chat.assert_not_awaited()
        assert await chat_service.consume_new_chat(db_session, first.id, False) is True
        consume_chat.assert_awaited_once_with(first.id)

        assert await chat_service.can_send_message(db_session, chat, second.id) == (True, None)
        assert await chat_service.can_send_message(db_session, chat, first.id) == (True, None)
        for _ in range(2):
            db_session.add(
                Message(
                    id=uuid4(),
                    chat_id=chat.id,
                    sender_id=first.id,
                    receiver_id=second.id,
                    message_type="text",
                )
            )
        await db_session.flush()
        allowed, reason = await chat_service.can_send_message(db_session, chat, first.id)
        assert allowed is False
        assert "accept the conversation" in reason

        chat.status = "accepted"
        assert await chat_service.can_send_message(db_session, chat, first.id) == (True, None)
        assert await chat_service.accept_chat(db_session, chat) is True
        assert chat.status == "accepted"


class TestMessageService:
    async def test_create_and_serialize_message(self, db_session, monkeypatch):
        first, second = _user(), _user()
        chat = _chat(first, second, status="accepted")
        db_session.add_all([first, second, chat])
        await db_session.commit()
        client_id = uuid4()
        replied_to = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=second.id,
            receiver_id=first.id,
            message_type="text",
        )
        db_session.add(replied_to)
        await db_session.flush()
        message = await chat_service.create_encrypted_message(
            db_session,
            chat.id,
            first.id,
            second.id,
            "hello",
            reply_to_id=replied_to.id,
            media_url="media/key",
            media_duration=12,
            client_id=client_id,
        )
        assert message._content != "hello"
        assert message.client_id == client_id

        monkeypatch.setattr(chat_service, "decrypt_message_async", AsyncMock(return_value="hello"))
        monkeypatch.setattr(
            chat_service.MediaService,
            "resolve_media_url",
            AsyncMock(return_value="https://signed.example/media"),
        )
        data = await chat_service.get_decrypted_message_for_client(
            db_session, message, first.id
        )
        assert data["content"] == "hello"
        assert data["media_url"] == "https://signed.example/media"
        assert data["client_id"] == str(client_id)
        assert data["reply_to_id"] == str(replied_to.id)

        found, content = await chat_service.get_message_for_admin(db_session, message.id)
        assert found.id == message.id
        assert content == "hello"
        missing, content = await chat_service.get_message_for_admin(db_session, uuid4())
        assert missing is None and content is None

    async def test_admin_message_without_content_and_delivery_read_updates(self, db_session):
        first, second = _user(), _user()
        chat = _chat(first, second, status="accepted")
        message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            _content=None,
        )
        db_session.add_all([first, second, chat, message])
        await db_session.commit()

        found, content = await chat_service.get_message_for_admin(db_session, message.id)
        assert found.id == message.id
        assert content is None
        await chat_service.mark_messages_as_delivered(db_session, [message.id], second.id)
        await chat_service.mark_messages_as_read(db_session, [message.id], second.id)
        await db_session.refresh(message)
        assert message.is_delivered is True
        assert message.is_read is True

    async def test_delete_message_authorization_scopes_and_media_failure(
        self, db_session, monkeypatch
    ):
        first, second, stranger = _user(), _user(), _user()
        chat = _chat(first, second, status="accepted")
        message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="photo",
            media_url="media/key",
        )
        old = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            sent_at=datetime.now(UTC) - timedelta(hours=2),
        )
        db_session.add_all([first, second, stranger, chat, message, old])
        await db_session.commit()

        assert await chat_service.delete_message(db_session, uuid4(), first.id, "me") == (
            False,
            "Message not found",
        )
        assert await chat_service.delete_message(db_session, message.id, stranger.id, "me") == (
            False,
            "Not authorized to delete this message",
        )
        assert await chat_service.delete_message(db_session, message.id, second.id, "everyone") == (
            False,
            "Only the sender can delete for everyone",
        )
        assert await chat_service.delete_message(db_session, old.id, first.id, "everyone") == (
            False,
            "Cannot delete for everyone after 1 hour. Use delete for me instead.",
        )

        delete_media = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        monkeypatch.setattr(chat_service.MediaService, "delete_media", delete_media)
        assert await chat_service.delete_message(db_session, message.id, first.id, "everyone") == (
            True,
            None,
        )
        assert message.is_deleted_for_all is True
        assert message._content == "[Message deleted]"
        delete_media.assert_awaited_once_with(str(chat.id), str(message.id), "photo")
        assert await chat_service.delete_message(db_session, old.id, second.id, "me") == (
            True,
            None,
        )
        assert old.is_deleted_for_receiver is True

    async def test_edit_message_validation_and_success(self, db_session):
        first, second = _user(), _user()
        chat = _chat(first, second, status="accepted")
        text_message = Message(
            id=uuid4(), chat_id=chat.id, sender_id=first.id, receiver_id=second.id, message_type="text"
        )
        photo_message = Message(
            id=uuid4(), chat_id=chat.id, sender_id=first.id, receiver_id=second.id, message_type="photo"
        )
        deleted_message = Message(
            id=uuid4(),
            chat_id=chat.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            is_deleted_for_all=True,
        )
        db_session.add_all([first, second, chat, text_message, photo_message, deleted_message])
        await db_session.commit()

        assert await chat_service.edit_message(db_session, uuid4(), first.id, "x") == (
            None,
            "Message not found",
        )
        assert await chat_service.edit_message(db_session, text_message.id, second.id, "x") == (
            None,
            "Not authorized to edit this message",
        )
        assert await chat_service.edit_message(db_session, photo_message.id, first.id, "x") == (
            None,
            "Only text messages can be edited",
        )
        assert await chat_service.edit_message(db_session, deleted_message.id, first.id, "x") == (
            None,
            "Cannot edit a deleted message",
        )
        assert await chat_service.edit_message(db_session, text_message.id, first.id, "  ") == (
            None,
            "Message content cannot be empty",
        )
        updated, error = await chat_service.edit_message(
            db_session, text_message.id, first.id, "  corrected  "
        )
        assert error is None
        assert updated.content == "corrected"
        assert updated.is_edited is True
        assert updated.edited_at is not None

    async def test_forward_message_validation_and_success(self, db_session, monkeypatch):
        first, second, stranger = _user(), _user(), _user()
        source = _chat(first, second, status="accepted")
        target = _chat(first, stranger, status="accepted")
        outsider_target = _chat(second, stranger, status="accepted")
        inactive_target = _chat(first, stranger, status="accepted", is_active=False)
        original = Message(
            id=uuid4(),
            chat_id=source.id,
            sender_id=first.id,
            receiver_id=second.id,
            message_type="text",
            _content="ciphertext",
        )
        db_session.add_all(
            [first, second, stranger, source, target, outsider_target, inactive_target, original]
        )
        await db_session.commit()

        assert await chat_service.forward_message(db_session, uuid4(), target.id, first.id) == (
            None,
            "Message not found",
        )
        assert await chat_service.forward_message(
            db_session, original.id, target.id, stranger.id
        ) == (None, "Not authorized to forward this message")
        assert await chat_service.forward_message(
            db_session, original.id, uuid4(), first.id
        ) == (None, "Target chat not found")
        assert await chat_service.forward_message(
            db_session, original.id, outsider_target.id, first.id
        ) == (None, "Not part of target chat")
        assert await chat_service.forward_message(
            db_session, original.id, inactive_target.id, first.id
        ) == (None, "Target chat not found")

        monkeypatch.setattr(
            chat_service, "decrypt_message_async", AsyncMock(return_value="original text")
        )
        forwarded, error = await chat_service.forward_message(
            db_session, original.id, target.id, first.id
        )
        assert error is None
        assert forwarded.chat_id == target.id
        assert forwarded.sender_id == first.id
        assert forwarded.receiver_id == stranger.id
        assert forwarded.content == "📨 Forwarded: original text"
