"""True-concurrency tests: requests race against real Postgres isolation.

Uses the `race_client` fixture — every in-flight request gets its OWN
AsyncSession/transaction, unlike `client` which shares one session (and
therefore can never reproduce DB-level races).
"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat import Chat
from app.models.daily_limit import DailyLimit
from app.models.match import Match
from app.models.swipe import Swipe
from app.models.user_profile import UserProfile
from tests.helpers import parallel_calls, parallel_requests
from tests.test_swipes import FEMALE_PROFILE, MALE_PROFILE, SWIPE_URL, _phone, register_user

CHAT_URL = "/api/v1/chats"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_free(db_session: AsyncSession, user_id: str) -> None:
    """Strip the welcome premium bonus so daily limits apply."""
    await db_session.execute(
        update(UserProfile).where(UserProfile.user_id == UUID(user_id)).values(premium_until=None)
    )
    await db_session.commit()


class TestConcurrentSwipes:
    async def test_concurrent_duplicate_swipe_single_row(
        self, race_client, mock_verification_code, db_session
    ):
        """N concurrent identical likes → exactly one swipe row, one 200."""
        male = await register_user(
            race_client, mock_verification_code, _phone("race_m1"), MALE_PROFILE
        )
        female = await register_user(
            race_client, mock_verification_code, _phone("race_f1"), FEMALE_PROFILE
        )
        female_id = female["user"]["id"]
        headers = _headers(male["access_token"])

        responses = await parallel_requests(
            race_client,
            "POST",
            SWIPE_URL,
            6,
            headers=headers,
            json_payload={"user_id": female_id, "direction": "like"},
        )
        codes = sorted(r.status_code for r in responses)
        assert codes.count(200) == 1, codes
        assert codes.count(400) == 5, codes

        rows = (await db_session.execute(select(Swipe))).scalars().all()
        assert len(rows) == 1
        assert rows[0].direction == "like"

    async def test_concurrent_mutual_likes_create_exactly_one_match(
        self, race_client, mock_verification_code, db_session
    ):
        """A→B and B→A fired simultaneously must still produce exactly one match.

        Regression: without pair serialization both requests ran in READ
        COMMITTED isolation, neither saw the other's uncommitted swipe, and
        the match was silently never created.
        """
        male = await register_user(
            race_client, mock_verification_code, _phone("race_m2"), MALE_PROFILE
        )
        female = await register_user(
            race_client, mock_verification_code, _phone("race_f2"), FEMALE_PROFILE
        )
        male_id, female_id = male["user"]["id"], female["user"]["id"]

        async def like_one(i: int):
            if i == 0:
                return await race_client.post(
                    SWIPE_URL,
                    json={"user_id": female_id, "direction": "like"},
                    headers=_headers(male["access_token"]),
                )
            return await race_client.post(
                SWIPE_URL,
                json={"user_id": male_id, "direction": "like"},
                headers=_headers(female["access_token"]),
            )

        responses = await parallel_calls(2, like_one)
        assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
        matched_flags = [r.json()["matched"] for r in responses]
        # The second request (after the first committed) sees the mutual like.
        assert matched_flags.count(True) == 1, matched_flags

        matches = (
            (
                await db_session.execute(
                    select(Match).where(
                        ((Match.user1_id == UUID(male_id)) & (Match.user2_id == UUID(female_id)))
                        | ((Match.user1_id == UUID(female_id)) & (Match.user2_id == UUID(male_id)))
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(matches) == 1
        assert matches[0].is_active is True

    async def test_concurrent_likes_respect_daily_limit(
        self, race_client, mock_verification_code, db_session, monkeypatch
    ):
        """5 concurrent likes with a limit of 2 → exactly 2 succeed, 3 get 429.

        consume_like's conditional UPDATE is the serialization point; this
        asserts it holds under true concurrency (not just sequential calls).
        """
        monkeypatch.setattr(settings, "FREE_USER_DAILY_LIKES", 2)
        male = await register_user(
            race_client, mock_verification_code, _phone("race_m3"), MALE_PROFILE
        )
        await _make_free(db_session, male["user"]["id"])
        females = []
        for i in range(5):
            f = await register_user(
                race_client, mock_verification_code, _phone(f"race_f3_{i}"), FEMALE_PROFILE
            )
            females.append(f["user"]["id"])
        headers = _headers(male["access_token"])

        async def like_target(i: int):
            return await race_client.post(
                SWIPE_URL,
                json={"user_id": females[i], "direction": "like"},
                headers=headers,
            )

        responses = await parallel_calls(5, like_target)
        codes = [r.status_code for r in responses]
        assert codes.count(200) == 2, codes
        assert codes.count(429) == 3, codes

        row = (
            await db_session.execute(
                select(DailyLimit).where(DailyLimit.user_id == UUID(male["user"]["id"]))
            )
        ).scalar_one()
        assert row.likes_used == 2

    async def test_pass_then_concurrent_relikes_single_row(
        self, race_client, mock_verification_code, db_session
    ):
        """pass → two concurrent likes: delete+insert path stays unique."""
        male = await register_user(
            race_client, mock_verification_code, _phone("race_m4"), MALE_PROFILE
        )
        female = await register_user(
            race_client, mock_verification_code, _phone("race_f4"), FEMALE_PROFILE
        )
        female_id = female["user"]["id"]
        headers = _headers(male["access_token"])

        res = await race_client.post(
            SWIPE_URL, json={"user_id": female_id, "direction": "pass"}, headers=headers
        )
        assert res.status_code == 200

        responses = await parallel_requests(
            race_client,
            "POST",
            SWIPE_URL,
            3,
            headers=headers,
            json_payload={"user_id": female_id, "direction": "like"},
        )
        codes = sorted(r.status_code for r in responses)
        assert codes.count(200) == 1, codes

        rows = (
            (
                await db_session.execute(
                    select(Swipe).where(
                        Swipe.from_user == UUID(male["user"]["id"]),
                        Swipe.to_user == UUID(female_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].direction == "like"


class TestConcurrentChats:
    async def test_concurrent_chat_creation_same_pair_single_chat(
        self, race_client, mock_verification_code, db_session
    ):
        """N concurrent POST /chats for the same pair → exactly one chat row."""
        male = await register_user(
            race_client, mock_verification_code, _phone("race_m5"), MALE_PROFILE
        )
        female = await register_user(
            race_client, mock_verification_code, _phone("race_f5"), FEMALE_PROFILE
        )
        female_id = female["user"]["id"]
        headers = _headers(male["access_token"])

        responses = await parallel_requests(
            race_client,
            "POST",
            CHAT_URL,
            3,  # pair-cap in Redis 429s the 4th+ within 24h
            headers=headers,
            json_payload={"user_id": female_id, "content": "race hello"},
        )
        codes = [r.status_code for r in responses]
        assert codes.count(200) >= 1, codes
        assert all(c in (200, 429) for c in codes), codes

        chats = (
            (
                await db_session.execute(
                    select(Chat).where(
                        (
                            (Chat.initiator_id == UUID(male["user"]["id"]))
                            & (Chat.recipient_id == UUID(female_id))
                        )
                        | (
                            (Chat.initiator_id == UUID(female_id))
                            & (Chat.recipient_id == UUID(male["user"]["id"]))
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(chats) == 1

        new_flags = [r.json().get("is_new") for r in responses if r.status_code == 200]
        assert new_flags.count(True) == 1, new_flags
