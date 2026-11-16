"""Phase 3 — query-count regression guards for hot list endpoints.

Each list endpoint's SQL statement count must stay bounded as the dataset
grows. N peers are created; if an N+1 creeps in, the count blows past the
ceiling below. Counts are ceilings, not exact numbers, so unrelated small
changes don't flake — but any per-row query pattern is caught.
"""

import hashlib

import pytest
from httpx import AsyncClient

from tests.helpers.test_infra import QueryCounter
from tests.photo_visibility_helper import grant_approved_photo
from tests.test_search import (
    COMPLETE_PROFILE_PAYLOAD_FEMALE,
    COMPLETE_PROFILE_PAYLOAD_MALE,
    REGISTER_COMPLETE_URL,
    VALID_CODE,
    VERIFY_CODE_URL,
)

N_PEERS = 8
N_UNMATCHED_PEERS = 6
# Measured baselines (8 matched + 6 unswiped peers): discover=5, search=6,
# matches=10, chats=14, notifications=2 — all constant, not growing with rows.
# Ceiling 20 keeps margin for small changes; any per-row query pattern (N+1)
# adds >= N_PEERS statements and trips it.
QUERY_CEILING = 20


def _phone(key: str) -> str:
    return "+9891" + str(int(hashlib.sha1(key.encode()).hexdigest(), 16) % 10**10).zfill(10)


async def _register(client: AsyncClient, phone: str, payload: dict, mock_verification_code):
    await mock_verification_code(phone, VALID_CODE)
    res = await client.post(VERIFY_CODE_URL, json={"phone": phone, "code": VALID_CODE})
    assert res.status_code == 200, res.text
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    res = await client.post(REGISTER_COMPLETE_URL, json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return headers, res.json()["user"]["id"]


@pytest.mark.usefixtures("disable_rate_limiting", "mock_websocket_manager")
async def test_list_endpoint_query_counts(
    client: AsyncClient,
    db_session,
    mock_verification_code,
):
    """Discover/search/matches/chats must not N+1 with a 14-user dataset."""
    male_phone = _phone("qcount_male@example.com")
    male_headers, male_id = await _register(
        client, male_phone, COMPLETE_PROFILE_PAYLOAD_MALE, mock_verification_code
    )
    await grant_approved_photo(male_id)

    peer_ids = []
    for i in range(N_PEERS + N_UNMATCHED_PEERS):
        payload = dict(COMPLETE_PROFILE_PAYLOAD_FEMALE, name=f"QueryCount Peer {i}")
        peer_phone = _phone(f"qcount_peer_{i}@example.com")
        peer_headers, peer_id = await _register(client, peer_phone, payload, mock_verification_code)
        await grant_approved_photo(peer_id)
        peer_ids.append(peer_id)
        if i >= N_PEERS:
            if i == N_PEERS:
                # This peer likes the male but gets no like-back → shows up
                # in /swipes/likers so that probe has a real row.
                res = await client.post(
                    "/api/v1/swipes",
                    json={"user_id": male_id, "direction": "like"},
                    headers=peer_headers,
                )
                assert res.status_code in (200, 201), res.text
            continue  # remaining peers stay unswiped → discover/search candidates
        # Peer likes the male first, so his like creates a match.
        res = await client.post(
            "/api/v1/swipes", json={"user_id": male_id, "direction": "like"}, headers=peer_headers
        )
        assert res.status_code in (200, 201), res.text
        # Male likes back → match.
        res = await client.post(
            "/api/v1/swipes", json={"user_id": peer_id, "direction": "like"}, headers=male_headers
        )
        assert res.status_code in (200, 201), res.text

    # Open a chat with each match and send a message, so /chats has real rows
    # (peers, photos, last messages, unread counters) to serialize.
    for peer_id in peer_ids[:N_PEERS]:
        res = await client.post(
            "/api/v1/chats", json={"user_id": peer_id, "content": "hi"}, headers=male_headers
        )
        assert res.status_code in (200, 201), res.text
        chat_id = res.json()["chat_id"]
        res = await client.post(
            f"/api/v1/messages/{chat_id}/text", json={"content": "hi"}, headers=male_headers
        )
        assert res.status_code in (200, 201), res.text

    # Warm-up: token validation caches, redis state, etc. Query counters
    # measure only the second (steady-state) request below.
    probe_urls = (
        "/api/v1/discover",
        "/api/v1/search",
        "/api/v1/matches",
        "/api/v1/chats",
        "/api/v1/swipes/liked",
        "/api/v1/swipes/likers",
    )
    for url in probe_urls:
        res = await client.get(url, headers=male_headers)
        assert res.status_code == 200, res.text

    measured = {}
    for url in probe_urls:
        # Detach everything loaded by the warm-up request: SQLAlchemy skips
        # relationship queries for objects already in the identity map, which
        # would make every endpoint look N+1-free when it isn't.
        db_session.expunge_all()
        with QueryCounter(db_session.get_bind()) as counter:
            res = await client.get(url, headers=male_headers)
            assert res.status_code == 200, res.text
        measured[url] = counter.count
        assert res.json()["total"] > 0, (
            f"{url} probe returned 0 rows — row-serialization path not measured"
        )

    # Notifications list: probe must have real rows (peers liking male created
    # "like" notifications), otherwise the count proves nothing.
    db_session.expunge_all()
    with QueryCounter(db_session.get_bind()) as counter:
        res = await client.get("/api/v1/notifications", headers=male_headers)
        assert res.status_code == 200, res.text
    measured["/api/v1/notifications"] = counter.count
    assert res.json()["total"] >= N_PEERS, (
        f"notifications probe empty (total={res.json()['total']}) — "
        "row-serialization path is not being measured"
    )

    violations = [f"{url}={count}" for url, count in measured.items() if count > QUERY_CEILING]
    assert not violations, (
        f"{len(violations)} endpoint(s) exceed ceiling {QUERY_CEILING} — likely an N+1 "
        f"with {N_PEERS} peers: {', '.join(violations)}"
    )
