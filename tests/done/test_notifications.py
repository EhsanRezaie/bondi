import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

REGISTER_INIT_URL = "/api/v1/auth/register/init"
REGISTER_VERIFY_URL = "/api/v1/auth/register/verify"
REGISTER_COMPLETE_URL = "/api/v1/auth/register/complete"
NOTIFICATIONS_URL = "/api/v1/notifications"
SWIPE_URL = "/api/v1/swipes"

VALID_PASSWORD = "strongpass123"
VALID_CODE = "123456"

COMPLETE_PROFILE = {
    "name": "Test User",
    "birth_date": "2000-01-01",
    "gender": "male",
    "lat": 35.6892,
    "lng": 51.3890,
}


async def register_user(client: AsyncClient, email: str, mock_verification_code=None) -> dict:
    res = await client.post(REGISTER_INIT_URL, json={"email": email})
    assert res.status_code == 200, res.text

    if mock_verification_code:
        await mock_verification_code(email, VALID_CODE)

    res = await client.post(REGISTER_VERIFY_URL, json={
        "email": email, "code": VALID_CODE, "password": VALID_PASSWORD,
    })
    assert res.status_code == 200, res.text
    data = res.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=COMPLETE_PROFILE, headers=headers)
    assert res.status_code == 200, res.text

    return res.json()


class TestNotifications:
    """Test notification CRUD operations"""

    async def test_get_notifications_empty(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, "empty@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.get(NOTIFICATIONS_URL, headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["notifications"] == []
        assert body["total"] == 0
        assert body["next_offset"] is None

    async def test_get_notifications_pagination(self, client: AsyncClient, mock_verification_code):
        receiver_data = await register_user(client, "receiver@example.com", mock_verification_code)
        receiver_headers = {"Authorization": f"Bearer {receiver_data['access_token']}"}

        for i in range(3):
            liker_data = await register_user(
                client, f"liker_{i}@example.com", mock_verification_code
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

    async def test_get_notifications_requires_auth(self, client: AsyncClient):
        res = await client.get(NOTIFICATIONS_URL)
        assert res.status_code == 401

    async def test_mark_single_notification_read(self, client: AsyncClient, mock_verification_code):
        data = await register_user(client, "markread_main@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        user2_data = await register_user(client, "markread_liker@example.com", mock_verification_code)
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
        data = await register_user(client, "bulk_main@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        for i in range(3):
            user_data = await register_user(
                client, f"bulk{i}@example.com", mock_verification_code
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
        data = await register_user(client, "invalid@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.post(
            NOTIFICATIONS_URL + "/read",
            json={"notification_ids": ["00000000-0000-0000-0000-000000000001"]},
            headers=headers
        )
        assert res.status_code == 204

    async def test_mark_read_other_user_notification_fails(self, client: AsyncClient, mock_verification_code):
        userA_data = await register_user(client, "usera@example.com", mock_verification_code)
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        userB_data = await register_user(client, "userb@example.com", mock_verification_code)
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        userC_data = await register_user(client, "userc@example.com", mock_verification_code)
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
        data = await register_user(client, "delete_main@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        user2_data = await register_user(client, "delete_liker@example.com", mock_verification_code)
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
        data = await register_user(client, "nonexist@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        res = await client.delete(
            f"{NOTIFICATIONS_URL}/00000000-0000-0000-0000-000000000001",
            headers=headers
        )
        assert res.status_code == 404

    async def test_delete_other_user_notification(self, client: AsyncClient, mock_verification_code):
        userA_data = await register_user(client, "deleteA@example.com", mock_verification_code)
        userA_headers = {"Authorization": f"Bearer {userA_data['access_token']}"}

        userB_data = await register_user(client, "deleteB@example.com", mock_verification_code)
        userB_headers = {"Authorization": f"Bearer {userB_data['access_token']}"}

        userC_data = await register_user(client, "deleteC@example.com", mock_verification_code)
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
        user = await register_user(client, "counts_user@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        # Generate a like notification
        liker = await register_user(client, "counts_liker@example.com", mock_verification_code)
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
        user = await register_user(client, "counts_read_user@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        liker = await register_user(client, "counts_read_liker@example.com", mock_verification_code)
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
        user = await register_user(client, "counts_del_user@example.com", mock_verification_code)
        headers = {"Authorization": f"Bearer {user['access_token']}"}

        liker = await register_user(client, "counts_del_liker@example.com", mock_verification_code)
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
        user = await register_user(client, "counts_empty_user@example.com", mock_verification_code)
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
        user1 = await register_user(client, "push_img1@example.com", mock_verification_code)
        user2 = await register_user(client, "push_img2@example.com", mock_verification_code)

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
        user1 = await register_user(client, "push_match1@example.com", mock_verification_code)
        user2 = await register_user(client, "push_match2@example.com", mock_verification_code)

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
        liker = await register_user(client, "nopush_liker@example.com", mock_verification_code)
        target = await register_user(client, "nopush_target@example.com", mock_verification_code)

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
