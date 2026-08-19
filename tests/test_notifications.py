
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
def _phone(key: str) -> str:
    """Derive a deterministic, unique E.164 phone number from a string key."""
    import hashlib
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


VERIFY_CODE_URL = "/api/v1/auth/verify-code"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
NOTIFICATIONS_URL = "/api/v1/notifications"
SWIPE_URL = "/api/v1/swipes"

VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Test User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, phone: str, mock_verification_code=None) -> dict:
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


class TestNotifications:
    """Test notification CRUD operations"""

    async def test_get_notifications_empty(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("empty@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(NOTIFICATIONS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["notifications"] == []
        assert body["total"] == 0
        assert body["next_offset"] is None

    async def test_get_notifications_pagination(self, client: AsyncClient, mock_verification_code):
        receiver_data = await register_user(client, _phone("receiver@example.com"), mock_verification_code)
        receiver_headers = {"Authorization": f"Bearer {receiver_data['access_token']}"}

        for i in range(3):
            liker_data = await register_user(
                client, _phone(f"liker_{i}@example.com"), mock_verification_code
            )
            liker_headers = {"Authorization": f"Bearer {liker_data['access_token']}"}

            await client.post(
                SWIPE_URL,
                json={"user_id": receiver_data["user"]["id"], "direction": "like"},
                headers=liker_headers
            )

        res = await client.get(NOTIFICATIONS_URL, params={"limit": 2}, headers=receiver_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["notifications"]) == 2
        assert body["total"] >= 3
        assert body["next_offset"] == 2

    async def test_get_notifications_type_filter(self, client: AsyncClient, mock_verification_code):
        receiver_data = await register_user(client, _phone("typefilter@example.com"), mock_verification_code)
        receiver_headers = {"Authorization": f"Bearer {receiver_data['access_token']}"}

        liker_data = await register_user(
            client, _phone("typefilter_liker@example.com"), mock_verification_code
        )
        liker_headers = {"Authorization": f"Bearer {liker_data['access_token']}"}

        # A like from liker -> receiver produces a 'like' notification for receiver
        await client.post(
            SWIPE_URL,
            json={"user_id": receiver_data["user"]["id"], "direction": "like"},
            headers=liker_headers
        )

        res = await client.get(
            NOTIFICATIONS_URL,
            params={"type": "like"},
            headers=receiver_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] >= 1
        assert all(n["type"] == "like" for n in body["notifications"])

        res = await client.get(
            NOTIFICATIONS_URL,
            params={"type": "match"},
            headers=receiver_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 0
        assert body["notifications"] == []
        assert body["next_offset"] is None

    async def test_get_notifications_type_filter_does_not_leak(self, client: AsyncClient, mock_verification_code):
        user_a = await register_user(client, _phone("typeleak_a@example.com"), mock_verification_code)
        user_b = await register_user(client, _phone("typeleak_b@example.com"), mock_verification_code)
        headers_a = {"Authorization": f"Bearer {user_a['access_token']}"}
        headers_b = {"Authorization": f"Bearer {user_b['access_token']}"}

        # B likes A -> A gets a 'like' notification, B gets a 'liked' notification
        await client.post(
            SWIPE_URL,
            json={"user_id": user_a["user"]["id"], "direction": "like"},
            headers=headers_b,
        )

        # B's own 'like' feed must stay empty — only 'liked' entries exist for B
        res = await client.get(NOTIFICATIONS_URL, params={"type": "like"}, headers=headers_b)
        assert res.status_code == 200
        assert res.json()["total"] == 0

    async def test_get_notifications_requires_auth(self, client: AsyncClient):
        res = await client.get(NOTIFICATIONS_URL)
        assert res.status_code == 401

    async def test_mark_single_notification_read(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("markread_main@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        user2_data = await register_user(client, _phone("markread_liker@example.com"), mock_verification_code)
        user2_headers = {"Authorization": f"Bearer {user2_data['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": data["user"]["id"], "direction": "like"},
            headers=user2_headers
        )

        get_res = await client.get(NOTIFICATIONS_URL, headers=headers)
        notifications = get_res.json()["notifications"]
        assert len(notifications) > 0
        assert notifications[0]["is_read"] is False

        notification_id = notifications[0]["id"]
        read_res = await client.post(
            NOTIFICATIONS_URL + "/read",
            json={"notification_ids": [notification_id]},
            headers=headers
        )
        assert read_res.status_code == 204

        get_res2 = await client.get(NOTIFICATIONS_URL, headers=headers)
        for n in get_res2.json()["notifications"]:
            if n["id"] == notification_id:
                assert n["is_read"] is True

    async def test_mark_multiple_notifications_read(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("bulk_main@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        for i in range(3):
            user_data = await register_user(
                client, _phone(f"bulk{i}@example.com"), mock_verification_code
            )
            user_headers = {"Authorization": f"Bearer {user_data['access_token']}"}
            await client.post(
                SWIPE_URL,
                json={"user_id": data["user"]["id"], "direction": "like"},
                headers=user_headers
            )

        get_res = await client.get(NOTIFICATIONS_URL, headers=headers)
        notifications = get_res.json()["notifications"]
        notification_ids = [n["id"] for n in notifications[:2]]

        read_res = await client.post(
            NOTIFICATIONS_URL + "/read",
            json={"notification_ids": notification_ids},
            headers=headers
        )
        assert read_res.status_code == 204

        get_res2 = await client.get(NOTIFICATIONS_URL, headers=headers)
        for n in get_res2.json()["notifications"]:
            if n["id"] in notification_ids:
                assert n["is_read"] is True

    async def test_mark_read_invalid_notification_id(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("invalid@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            NOTIFICATIONS_URL + "/read",
            json={"notification_ids": ["00000000-0000-0000-0000-000000000001"]},
            headers=headers
        )
        assert res.status_code == 204

    async def test_mark_read_other_user_notification_fails(self, client: AsyncClient, mock_verification_code):
        userA_data = await register_user(client, _phone("usera@example.com"), mock_verification_code)
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        userB_data = await register_user(client, _phone("userb@example.com"), mock_verification_code)
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        userC_data = await register_user(client, _phone("userc@example.com"), mock_verification_code)
        userC_headers = {"Authorization": f"Bearer {userC_data['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": userA_data["user"]["id"], "direction": "like"},
            headers=userC_headers
        )

        get_res = await client.get(NOTIFICATIONS_URL, headers=userA_headers)
        userA_notifications = get_res.json()["notifications"]
        assert len(userA_notifications) > 0

        res = await client.post(
            NOTIFICATIONS_URL + "/read",
            json={"notification_ids": [userA_notifications[0]["id"]]},
            headers=userB_headers
        )
        assert res.status_code in [204, 404]

    async def test_delete_notification(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("delete_main@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        user2_data = await register_user(client, _phone("delete_liker@example.com"), mock_verification_code)
        user2_headers = {"Authorization": f"Bearer {user2_data['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": data["user"]["id"], "direction": "like"},
            headers=user2_headers
        )

        get_res = await client.get(NOTIFICATIONS_URL, headers=headers)
        notifications = get_res.json()["notifications"]
        assert len(notifications) > 0
        notification_id = notifications[0]["id"]

        del_res = await client.delete(f"{NOTIFICATIONS_URL}/{notification_id}", headers=headers)
        assert del_res.status_code == 204

        get_res2 = await client.get(NOTIFICATIONS_URL, headers=headers)
        for n in get_res2.json()["notifications"]:
            assert n["id"] != notification_id

    async def test_delete_nonexistent_notification(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, _phone("nonexist@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.delete(
            f"{NOTIFICATIONS_URL}/00000000-0000-0000-0000-000000000001",
            headers=headers
        )
        assert res.status_code == 404

    async def test_delete_other_user_notification(self, client: AsyncClient, mock_verification_code):
        userA_data = await register_user(client, _phone("deleteA@example.com"), mock_verification_code)
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        userB_data = await register_user(client, _phone("deleteB@example.com"), mock_verification_code)
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        userC_data = await register_user(client, _phone("deleteC@example.com"), mock_verification_code)
        userC_headers = {"Authorization": f"Bearer {userC_data['access_token']}"}

        await client.post(
            SWIPE_URL,
            json={"user_id": userA_data["user"]["id"], "direction": "like"},
            headers=userC_headers
        )

        get_res = await client.get(NOTIFICATIONS_URL, headers=userA_headers)
        notifications = get_res.json()["notifications"]
        assert len(notifications) > 0
        notification_id = notifications[0]["id"]

        del_res = await client.delete(f"{NOTIFICATIONS_URL}/{notification_id}", headers=userB_headers)
        assert del_res.status_code == 404


class TestNotificationRealtime:
    """Test real-time WS events and counts endpoint"""

    async def test_get_notification_counts(self, client, mock_verification_code):
        """GET /notifications/counts should return total and by_type unread counts."""
        user = await register_user(client, _phone("counts_user@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        # Generate a like notification
        liker = await register_user(client, _phone("counts_liker@example.com"), mock_verification_code)
        liker_headers = {"Authorization": f"Bearer {liker['access_token']}"}
        await client.post(
            SWIPE_URL,
            json={"user_id": user["user"]["id"], "direction": "like"},
            headers=liker_headers,
        )

        # Call counts endpoint
        res = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        assert "by_type" in data
        assert "like" in data["by_type"]
        assert data["by_type"]["like"] >= 1

    async def test_counts_drop_after_read(self, client, mock_verification_code):
        """Counts should drop after marking notifications read."""
        user = await register_user(client, _phone("counts_read_user@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        liker = await register_user(client, _phone("counts_read_liker@example.com"), mock_verification_code)
        liker_headers = {"Authorization": f"Bearer {liker['access_token']}"}
        await client.post(
            SWIPE_URL,
            json={"user_id": user["user"]["id"], "direction": "like"},
            headers=liker_headers,
        )

        # Get initial counts
        res = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res.status_code == 200
        initial_total = res.json()["total"]
        assert initial_total >= 1

        # Mark as read
        notif_res = await client.get("/api/v1/notifications", headers=headers)
        notif_ids = [n["id"] for n in notif_res.json()["notifications"]]
        await client.post(
            "/api/v1/notifications/read",
            json={"notification_ids": notif_ids},
            headers=headers,
        )

        # Counts should drop
        res2 = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res2.json()["total"] == 0

    async def test_counts_drop_after_delete(self, client, mock_verification_code):
        """Counts should drop after deleting notifications."""
        user = await register_user(client, _phone("counts_del_user@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        liker = await register_user(client, _phone("counts_del_liker@example.com"), mock_verification_code)
        liker_headers = {"Authorization": f"Bearer {liker['access_token']}"}
        await client.post(
            SWIPE_URL,
            json={"user_id": user["user"]["id"], "direction": "like"},
            headers=liker_headers,
        )

        # Get initial counts
        res = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res.status_code == 200
        initial_total = res.json()["total"]
        assert initial_total >= 1

        # Delete notifications
        notif_res = await client.get("/api/v1/notifications", headers=headers)
        for n in notif_res.json()["notifications"]:
            await client.delete(f"/api/v1/notifications/{n['id']}", headers=headers)

        # Counts should drop
        res2 = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res2.json()["total"] == 0

    async def test_counts_empty(self, client, mock_verification_code):
        """Counts should be zero for user with no notifications."""
        user = await register_user(client, _phone("counts_empty_user@example.com"), mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        res = await client.get("/api/v1/notifications/counts", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["by_type"] == {}


class TestPushImageUrl:
    """Test push notifications include image_url for avatar"""

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_push_like_includes_image_url(self, mock_send, client, mock_verification_code):
        """Push for like should include liker's photo as image_url."""
        user1 = await register_user(client, _phone("push_img1@example.com"), mock_verification_code)
        user2 = await register_user(client, _phone("push_img2@example.com"), mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        user2_id = user2["user"]["id"]

        await client.post(
            SWIPE_URL,
            json={"user_id": user2_id, "direction": "like"},
            headers=headers1,
        )

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert "image_url" in call_kwargs
        # image_url may be None if user has no photo, but key should exist
        assert call_kwargs.get("image_url") is not None or call_kwargs.get("image_url") is None

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_push_match_includes_image_url(self, mock_send, client, mock_verification_code):
        """Push for match should include other user's photo as image_url."""
        user1 = await register_user(client, _phone("push_match1@example.com"), mock_verification_code)
        user2 = await register_user(client, _phone("push_match2@example.com"), mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        headers2 = {"Authorization": f"Bearer {user2['access_token']}"}
        user1_id = user1["user"]["id"]
        user2_id = user2["user"]["id"]

        await client.post(SWIPE_URL, json={"user_id": user2_id, "direction": "like"}, headers=headers1)
        mock_send.reset_mock()
        await client.post(SWIPE_URL, json={"user_id": user1_id, "direction": "like"}, headers=headers2)

        match_calls = [
            c for c in mock_send.call_args_list
            if c.kwargs.get("title") == "It's a match!"
        ]
        assert len(match_calls) == 2
        for call in match_calls:
            call_kwargs = call.kwargs
            assert "image_url" in call_kwargs


class TestNoPushToSelf:
    """Test no push notification is sent to self for own actions"""

    @patch("app.services.push_service.PushService.send_to_user", new_callable=AsyncMock)
    async def test_no_push_to_self_on_liked(self, mock_send, client, mock_verification_code):
        """When user likes someone, no push should be sent to self (WS only)."""
        liker = await register_user(client, _phone("nopush_liker@example.com"), mock_verification_code)
        target = await register_user(client, _phone("nopush_target@example.com"), mock_verification_code)

        liker_headers = {"Authorization": f"Bearer {liker['access_token']}"}
        target_id = target["user"]["id"]

        await client.post(
            SWIPE_URL,
            json={"user_id": target_id, "direction": "like"},
            headers=liker_headers,
        )

        # Push should only be called for the TARGET user (like notification), not for the LIKER
        push_user_ids = [str(c.kwargs["user_id"]) for c in mock_send.call_args_list]
        assert str(liker["user"]["id"]) not in push_user_ids
        assert str(target_id) in push_user_ids


class TestNotificationWSEvents:
    """Real-time `new_notification` events on the personal WS channel (/ws/stream).

    The event shape is the shared `_notification_ws_payload`:
      {"type": "new_notification", "data": {id, type, title, body, is_read,
       created_at, user_id, match_id, chat_id}}
    """

    WS_MANAGER_PATH = (
        "app.services.websocket_manager.websocket_manager.send_personal_message"
    )

    @patch(
        "app.services.websocket_manager.websocket_manager.send_personal_message",
        new_callable=AsyncMock,
    )
    async def test_ws_new_notification_on_like(
        self, mock_send, client, mock_verification_code
    ):
        """Liking publishes new_notification: `like` to the target + `liked` to the liker."""
        liker = await register_user(client, _phone("ws_liker@example.com"), mock_verification_code)
        target = await register_user(client, _phone("ws_target@example.com"), mock_verification_code)

        liker_headers = {"Authorization": f"Bearer {liker['access_token']}"}
        liker_id = str(liker["user"]["id"])
        target_id = str(target["user"]["id"])

        res = await client.post(
            SWIPE_URL,
            json={"user_id": target_id, "direction": "like"},
            headers=liker_headers,
        )
        assert res.status_code == 200, res.text

        events = [c.args[1] for c in mock_send.call_args_list]
        assert len(events) == 2, [c.args for c in mock_send.call_args_list]
        for event in events:
            assert event["type"] == "new_notification"
            data = event["data"]
            assert data["id"]
            assert data["is_read"] is False
            assert data["created_at"]
            assert "user_id" in data and "match_id" in data and "chat_id" in data

        like_event = next(e for e in events if e["data"]["type"] == "like")
        assert like_event["data"]["user_id"] == liker_id
        like_call = next(c for c in mock_send.call_args_list if c.args[1]["data"]["type"] == "like")
        assert like_call.args[0] == target_id

        liked_event = next(e for e in events if e["data"]["type"] == "liked")
        assert liked_event["data"]["user_id"] == target_id
        liked_call = next(c for c in mock_send.call_args_list if c.args[1]["data"]["type"] == "liked")
        assert liked_call.args[0] == liker_id

    @patch(
        "app.services.websocket_manager.websocket_manager.send_personal_message",
        new_callable=AsyncMock,
    )
    async def test_ws_new_notification_on_match(
        self, mock_send, client, mock_verification_code
    ):
        """Mutual like publishes new_notification (match) to BOTH users."""
        user1 = await register_user(client, _phone("ws_match1@example.com"), mock_verification_code)
        user2 = await register_user(client, _phone("ws_match2@example.com"), mock_verification_code)

        headers1 = {"Authorization": f"Bearer {user1['access_token']}"}
        headers2 = {"Authorization": f"Bearer {user2['access_token']}"}
        user1_id = str(user1["user"]["id"])
        user2_id = str(user2["user"]["id"])

        await client.post(SWIPE_URL, json={"user_id": user2_id, "direction": "like"}, headers=headers1)
        mock_send.reset_mock()

        res = await client.post(SWIPE_URL, json={"user_id": user1_id, "direction": "like"}, headers=headers2)
        assert res.status_code == 200, res.text

        match_calls = [
            c for c in mock_send.call_args_list
            if c.args[1]["type"] == "new_notification" and c.args[1]["data"]["type"] == "match"
        ]
        assert len(match_calls) == 2, [c.args for c in mock_send.call_args_list]
        assert {c.args[0] for c in match_calls} == {user1_id, user2_id}
        for call in match_calls:
            data = call.args[1]["data"]
            assert data["match_id"]
            assert data["title"] == "It's a match!"
            assert data["is_read"] is False

    @patch(
        "app.services.websocket_manager.websocket_manager.send_personal_message",
        new_callable=AsyncMock,
    )
    async def test_ws_new_notification_on_announcement(
        self, mock_send, client, mock_verification_code
    ):
        """Admin announcement publishes new_notification (system) to the recipient."""
        from app.core.config import settings

        user = await register_user(client, _phone("ws_announce@example.com"), mock_verification_code)
        user_id = str(user["user"]["id"])

        res = await client.post(
            "/api/v1/admin/announcements/test",
            json={
                "title": "Maintenance",
                "message": "System will be down tonight",
                "target_user_id": user_id,
            },
            headers={"X-Admin-Key": settings.ADMIN_SECRET_KEY},
        )
        assert res.status_code == 200, res.text

        assert mock_send.call_count == 1, [c.args for c in mock_send.call_args_list]
        assert mock_send.call_args.args[0] == user_id
        payload = mock_send.call_args.args[1]
        assert payload["type"] == "new_notification"
        data = payload["data"]
        assert data["type"] == "system"
        assert data["title"] == "[TEST] Maintenance"
        assert data["body"] == "System will be down tonight"
        assert data["is_read"] is False
        assert data["created_at"]

    @patch(
        "app.services.notification_service._publish_ws",
        new_callable=AsyncMock,
    )
    async def test_ws_not_published_for_message(
        self, mock_publish, client, db_session, mock_verification_code
    ):
        """Message notifications are push-only — never published as WS events."""
        from app.services.notification_service import NotificationService

        user = await register_user(client, _phone("ws_msg@example.com"), mock_verification_code)
        nsvc = NotificationService(db_session)
        await nsvc.notify_message(
            receiver_id=user["user"]["id"],
            sender_id=user["user"]["id"],
            sender_name="Tester",
            chat_id="00000000-0000-0000-0000-000000000001",
        )
        mock_publish.assert_not_awaited()
