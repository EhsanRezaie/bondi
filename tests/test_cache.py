import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.core import cache
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings


class TestBasicCacheOperations:
    async def test_cache_get_set_delete_and_failures(self):
        redis = MagicMock()
        redis.get = AsyncMock()
        redis.set = AsyncMock()
        redis.delete = AsyncMock()
        redis.get.return_value = '{"value": 1}'
        assert await cache.cache_get(redis, "key") == {"value": 1}

        redis.get.return_value = "not-json"
        assert await cache.cache_get(redis, "key") is None
        redis.get.side_effect = RuntimeError("redis unavailable")
        assert await cache.cache_get(redis, "key") is None

        redis.get.side_effect = None
        await cache.cache_set(redis, "key", {"value": 2}, 30)
        redis.set.assert_awaited_once_with("key", '{"value": 2}', ex=30)
        redis.set.side_effect = RuntimeError("redis unavailable")
        await cache.cache_set(redis, "key", {"value": 2}, 30)

        redis.delete.side_effect = None
        await cache.cache_delete(redis, "key")
        redis.delete.assert_awaited_once_with("key")
        redis.delete.side_effect = RuntimeError("redis unavailable")
        await cache.cache_delete(redis, "key")

        assert await cache.cache_get(redis, "missing") is None
        redis.set.side_effect = None
        await cache.cache_set(redis, "custom", {"date": date.today()}, 10)

    async def test_user_cache_invalidation_deletes_all_user_views(self):
        redis = MagicMock()
        redis.delete = AsyncMock()
        user_id = uuid4()

        await cache.invalidate_user_cache(redis, user_id)

        redis.delete.assert_awaited_once_with(
            cache.key_user_profile(user_id),
            cache.key_user_photos(user_id),
            cache.key_public_profile(user_id),
        )
        redis.delete.side_effect = RuntimeError("redis unavailable")
        await cache.invalidate_user_cache(redis, user_id)


class TestDiscoverCache:
    async def test_deck_read_save_remove_and_invalidate(self):
        redis = MagicMock()
        redis.get = AsyncMock()
        redis.set = AsyncMock()
        redis.ttl = AsyncMock(return_value=-1)
        redis.delete = AsyncMock()
        user_id, first, second = uuid4(), uuid4(), uuid4()
        deck = {"sig": "filters", "ids": [str(first), str(second)], "total": 2}
        redis.get.return_value = json.dumps(deck)

        assert await cache.get_discover_deck(redis, user_id) == deck
        await cache.remove_from_discover_deck(redis, user_id, first)
        key = cache.key_discover_deck(user_id)
        redis.ttl.assert_awaited_once_with(key)
        assert json.loads(redis.set.await_args.args[1]) == {
            "sig": "filters",
            "ids": [str(second)],
            "total": 1,
        }
        assert redis.set.await_args.kwargs == {"ex": cache.DISCOVER_DECK_TTL}

        redis.set.reset_mock()
        redis.ttl.return_value = 42
        redis.get.return_value = json.dumps(deck)
        await cache.remove_from_discover_deck(redis, user_id, first)
        assert redis.set.await_args.kwargs == {"ex": 42}

        await cache.save_discover_deck(redis, user_id, "sig", [str(second)], 1)
        assert json.loads(redis.set.await_args.args[1]) == {
            "sig": "sig",
            "ids": [str(second)],
            "total": 1,
        }
        await cache.invalidate_discover_deck(redis, user_id)
        redis.delete.assert_awaited_once_with(key)

    async def test_deck_remove_noop_and_best_effort_error_paths(self):
        redis = MagicMock()
        redis.get = AsyncMock()
        redis.set = AsyncMock()
        redis.ttl = AsyncMock()
        redis.delete = AsyncMock()
        user_id, target_id = uuid4(), uuid4()
        key = cache.key_discover_deck(user_id)

        redis.get.return_value = None
        await cache.remove_from_discover_deck(redis, user_id, target_id)
        redis.get.return_value = json.dumps({"ids": [str(uuid4())], "total": 1})
        await cache.remove_from_discover_deck(redis, user_id, target_id)
        redis.ttl.assert_not_awaited()

        redis.get.return_value = "bad-json"
        assert await cache.get_discover_deck(redis, user_id) is None
        await cache.remove_from_discover_deck(redis, user_id, target_id)
        redis.get.side_effect = RuntimeError("redis unavailable")
        assert await cache.get_discover_deck(redis, user_id) is None
        redis.get.side_effect = None
        redis.set.side_effect = RuntimeError("redis unavailable")
        await cache.save_discover_deck(redis, user_id, "sig", [], 0)
        redis.set.side_effect = None
        redis.delete.side_effect = RuntimeError("redis unavailable")
        await cache.invalidate_discover_deck(redis, user_id)

        assert key == cache.key_discover_deck(user_id)


class TestPhotoEpochAndSwipeCache:
    async def test_photo_epoch_defaults_and_bumps(self):
        redis = MagicMock()
        redis.get = AsyncMock()
        redis.incr = AsyncMock()
        redis.get.return_value = "8"
        assert await cache.get_photo_epoch(redis) == 8
        redis.get.return_value = None
        assert await cache.get_photo_epoch(redis) == 0
        redis.get.return_value = "not-an-integer"
        assert await cache.get_photo_epoch(redis) == 0
        redis.get.side_effect = RuntimeError("redis unavailable")
        assert await cache.get_photo_epoch(redis) == 0
        await cache.bump_photo_epoch(redis)
        redis.incr.assert_awaited_once_with(cache.key_photo_epoch())
        redis.incr.side_effect = RuntimeError("redis unavailable")
        await cache.bump_photo_epoch(redis)

    async def test_swipe_cache_records_reads_and_falls_back_on_error(self):
        redis = MagicMock()
        redis.sadd = AsyncMock()
        redis.expire = AsyncMock()
        redis.smembers = AsyncMock()
        swiper_id, swipee_id = uuid4(), uuid4()
        redis.smembers.return_value = {str(swipee_id), "other"}

        await cache.record_swipe_cache(redis, swiper_id, swipee_id)
        redis.sadd.assert_awaited_once_with(f"swiped:{swiper_id}", str(swipee_id))
        redis.expire.assert_awaited_once_with(f"swiped:{swiper_id}", cache.SWIPED_SET_TTL)
        assert await cache.get_swiped_ids(redis, swiper_id) == {str(swipee_id), "other"}

        redis.smembers.side_effect = RuntimeError("redis unavailable")
        assert await cache.get_swiped_ids(redis, swiper_id) == set()
        redis.sadd.side_effect = RuntimeError("redis unavailable")
        await cache.record_swipe_cache(redis, swiper_id, swipee_id)
        redis.smembers.side_effect = None
        redis.smembers.return_value = {b"bytes-id"}
        assert await cache.get_swiped_ids(redis, swiper_id) == {b"bytes-id"}


class TestAuthUserSnapshot:
    async def test_auth_snapshot_roundtrip_rederives_profile_properties(self):
        user_id = uuid4()
        birth_date = date.today() - timedelta(days=365 * 30)
        profile = UserProfile(
            user_id=user_id,
            name="Test User",
            birth_date=birth_date,
            gender="male",
            lat=35.0,
            lng=51.0,
            premium_until=datetime.now(UTC) + timedelta(days=1),
        )
        settings = UserSettings(user_id=user_id, hide_last_seen=True)
        user = User(
            id=user_id,
            phone="+989123456789",
            is_active=True,
            token_version=3,
            registration_status="onboarding_complete",
            profile=profile,
            settings=settings,
        )
        redis = AsyncMock()

        await cache.cache_auth_user(redis, user, 3)

        key, raw = redis.set.await_args.args
        assert key == cache.key_auth_user(user_id, 3)
        assert redis.set.await_args.kwargs == {"ex": cache.TTL_AUTH_USER}
        stored = json.loads(raw)
        assert stored["profile"]["birth_date"] == birth_date.isoformat()
        assert stored["settings"]["hide_last_seen"] is True

        redis.get.return_value = raw
        snapshot = await cache.get_cached_auth_user(redis, user_id, 3)
        assert snapshot.id == user_id
        assert snapshot.phone == user.phone
        assert snapshot.profile.birth_date == birth_date
        assert snapshot.profile.age >= 29
        assert snapshot.profile.is_premium is True
        assert snapshot.profile.is_profile_complete is True
        assert snapshot.settings.hide_last_seen is True

    async def test_auth_snapshot_handles_misses_bad_payload_and_invalidation(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        redis.delete = AsyncMock()
        user_id = uuid4()
        assert await cache.get_cached_auth_user(redis, user_id, 1) is None

        redis.get.return_value = "not-json"
        assert await cache.get_cached_auth_user(redis, user_id, 1) is None
        redis.get.side_effect = RuntimeError("redis unavailable")
        assert await cache.get_cached_auth_user(redis, user_id, 1) is None

        redis.get.side_effect = None
        redis.get.return_value = '{"id": "bad"}'
        assert await cache.get_cached_auth_user(redis, user_id, 1) is None

        async def scan_iter(match):
            assert match == f"cache:auth:{user_id}:v*"
            yield f"cache:auth:{user_id}:v1"
            yield f"cache:auth:{user_id}:v2"

        redis.scan_iter = scan_iter
        await cache.invalidate_auth_user(redis, user_id)
        assert [call.args[0] for call in redis.delete.await_args_list] == [
            f"cache:auth:{user_id}:v1",
            f"cache:auth:{user_id}:v2",
        ]

        async def failing_scan(**_kwargs):
            raise RuntimeError("redis unavailable")
            yield

        redis.scan_iter = failing_scan
        await cache.invalidate_auth_user(redis, user_id)

    def test_profile_cache_derives_incomplete_and_expired_fields(self):
        profile = cache._profile_from_cache(
            {
                "birth_date": None,
                "premium_until": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "name": None,
                "gender": None,
                "lat": None,
                "lng": None,
            }
        )

        assert profile.age == 0
        assert profile.is_premium is False
        assert profile.is_profile_complete is False
        assert cache._row_to_dict(None) is None
        assert cache._dict_to_obj(UserProfile, {}) is None
        profile_row = UserProfile(
            user_id=uuid4(),
            name="Cached User",
            birth_date=date.today(),
            gender="female",
        )
        assert cache._row_to_dict(profile_row)["name"] == "Cached User"

    def test_datetime_and_date_fields_are_reconstructed_from_strings(self):
        user_id = uuid4()
        profile = cache._dict_to_obj(
            UserProfile,
            {
                "birth_date": "2000-01-02",
                "premium_until": "2030-02-03T04:05:06+00:00",
                "user_id": str(user_id),
            },
        )
        assert profile.birth_date == date(2000, 1, 2)
        assert profile.premium_until == datetime.fromisoformat("2030-02-03T04:05:06+00:00")
        assert profile.user_id == str(user_id)

    def test_auth_user_snapshot_allows_missing_profile_and_settings(self):
        user_id = uuid4()
        snapshot = cache._user_from_cache(
            {"id": str(user_id), "is_active": True, "token_version": 1}
        )
        assert snapshot.id == user_id
        assert snapshot.profile is None
        assert snapshot.settings is None
