import base64
import json
from datetime import date, datetime, timedelta
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Float, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.core.redis as redis_module
from app.core.cache import (
    DISCOVER_DECK_CAP,
    get_discover_deck,
    get_photo_epoch,
    get_swiped_ids,
    save_discover_deck,
)
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.db.session import get_session
from app.db.types import Geography
from app.models.block import Block
from app.models.match import Match
from app.models.swipe import Swipe
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.discover import DiscoverResponse
from app.services.profile_service import haversine_km, serialize_card
from app.utils.geo import fuzz_distance

logger = get_logger("discover")

router = APIRouter(prefix="/discover", tags=["discover"])


def _encode_cursor(key, user_id):
    """Encode a keyset cursor into an opaque URL-safe token."""
    payload = json.dumps({"k": key, "id": str(user_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str):
    """Decode a keyset cursor. Returns (key, user_id) or (None, None)."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return data.get("k"), data.get("id")
    except Exception:
        return None, None


def _deck_sig(
    profile,
    age_min: int,
    age_max: int,
    gender: str | None,
    distance_km: int | None,
    photo_epoch: int,
) -> str:
    """Filter signature for the discover deck cache. Coords rounded to ~110 m
    so GPS jitter doesn't thrash the cache; any real filter change (or photo
    visibility event) misses."""
    lat, lng = profile.lat, profile.lng
    coords = f"{lat:.3f}:{lng:.3f}" if lat is not None and lng is not None else "none"
    return f"{age_min}|{age_max}|{gender}|{distance_km}|{coords}|{photo_epoch}"


@router.get("", response_model=DiscoverResponse)
@limiter.limit("60/minute")
async def discover(
    request: Request,
    age_min: int = Query(18, ge=18, le=100),
    age_max: int = Query(100, ge=18, le=100),
    gender: str = Query(None, pattern="^(male|female)$"),
    distance_km: int | None = Query(None, ge=1, le=500),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    cursor: str = Query(None, description="Opaque keyset cursor for stable pagination"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DiscoverResponse:
    """
    Discover page for swiping.
    Users you've swiped on (like OR pass) are excluded.
    Users you blocked are excluded.
    Users who blocked you are excluded.
    Users you already matched with are excluded.
    Filters by gender if provided, otherwise shows all genders.
    Distance filter is applied at the database level.
    """

    if not current_user.profile:
        return DiscoverResponse(users=[], total=0, next_offset=None)

    current_profile = current_user.profile

    # 1.x-style models make mypy see instance `.id` as Column[UUID]; cast to
    # the real runtime type once and use it for cache helpers (no new mypy
    # noise — and it retires the frozen get_swiped_ids variant below).
    viewer_id = cast(UUID, current_user.id)

    today = date.today()
    max_birth_date = today - timedelta(days=age_min * 365)
    min_birth_date = today - timedelta(days=(age_max + 1) * 365)

    # --- Swipe deduplication via Redis set (fast path) ---
    swiped_ids = await get_swiped_ids(redis_module.redis_client, viewer_id)

    blocked_user_ids = (
        select(Block.blocked_id).where(Block.blocker_id == current_user.id).subquery()
    )

    blocked_by_user_ids = (
        select(Block.blocker_id).where(Block.blocked_id == current_user.id).subquery()
    )

    matched_as_user1 = select(Match.user2_id).where(
        Match.user1_id == current_user.id, Match.is_active == True
    )
    matched_as_user2 = select(Match.user1_id).where(
        Match.user2_id == current_user.id, Match.is_active == True
    )
    matched_user_ids = matched_as_user1.union(matched_as_user2).subquery()

    # Shared filter conditions — used by BOTH the deck refill (cheap id-only
    # query) and the direct-DB fallback so the two can never diverge.
    conditions: list = [
        User.is_active == True,
        User.id != current_user.id,
        UserProfile.birth_date.between(min_birth_date, max_birth_date),
        User.id.not_in(select(blocked_user_ids.c.blocked_id)),
        User.id.not_in(select(blocked_by_user_ids.c.blocker_id)),
        User.id.not_in(select(matched_user_ids.c.user2_id)),
        # Visibility policy: only show users whose photos are all approved.
        # Denormalized into user_profiles.is_searchable (maintained at every
        # photo status event) — replaces two photo semi/anti-joins per refill.
        UserProfile.is_searchable == True,
    ]

    # Exclude swiped users via Redis set (if populated), else fall back to DB
    if swiped_ids:
        conditions.append(User.id.not_in(list(swiped_ids)))
    else:
        # Redis set empty or unavailable — fall back to DB subquery
        swiped_db_ids = select(Swipe.to_user).where(Swipe.from_user == current_user.id).subquery()
        conditions.append(User.id.not_in(select(swiped_db_ids.c.to_user)))

    if gender:
        conditions.append(UserProfile.gender == gender)

    has_coords = current_profile.lat is not None and current_profile.lng is not None
    distance_expr = None
    if has_coords:
        # PostGIS: viewer's point as geography; ST_DWithin/ST_Distance are
        # index-assisted via idx_profiles_location_gist — no per-row trig.
        # (Replaces the old bounding-box + haversine-expression pair.)
        viewer_point = func.ST_SetSRID(
            func.ST_MakePoint(current_profile.lng, current_profile.lat), 4326
        ).cast(Geography)
        # ST_Distance on geography returns meters → km for the API.
        distance_expr = func.ST_Distance(UserProfile.location, viewer_point) / 1000.0
        if distance_km is not None:
            conditions.append(
                func.ST_DWithin(UserProfile.location, viewer_point, distance_km * 1000)
            )

    # Deterministic, stable ordering: most recently online first, users who
    # never went online sink to the bottom (NULLS LAST). The id tiebreaker
    # guarantees a unique order so page boundaries never duplicate rows.
    col = User.last_seen_at
    order = (col.desc().nullslast(), User.id.desc())

    # Keyset (cursor) pagination vs legacy offset pagination.
    cursor_key, cursor_id = None, None
    use_cursor = False
    if cursor:
        parsed_key, parsed_id = _decode_cursor(cursor)
        if parsed_id:
            cursor_id = parsed_id
            cursor_key = parsed_key
            if cursor_key is not None:
                try:
                    cursor_key = datetime.fromisoformat(cursor_key)
                except Exception:
                    cursor_key = None
            use_cursor = True

    # ── Discover deck (Redis cache) ─────────────────────────────────────────
    # The ordered candidate-ID list is cached per user+filter signature, so a
    # page view costs one Redis read + one small PK fetch instead of a full
    # filtered scan + a full count. The DB is only touched on refill/fallback.
    sig = _deck_sig(
        current_profile,
        age_min,
        age_max,
        gender,
        distance_km,
        await get_photo_epoch(redis_module.redis_client),
    )
    deck = await get_discover_deck(redis_module.redis_client, viewer_id)
    if deck and deck.get("sig") != sig:
        deck = None

    if deck is None:
        # Refill: one count + one id-only query (no selectinloads, no distance
        # column) — replaces the per-request full scan + full count.
        base_query = (
            select(User.id).join(UserProfile, User.id == UserProfile.user_id).where(*conditions)
        )
        total = int(await session.scalar(select(func.count()).select_from(base_query.subquery())))
        id_rows = await session.execute(base_query.order_by(*order).limit(DISCOVER_DECK_CAP))
        deck_ids = [str(r[0]) for r in id_rows.all()]
        deck = {"sig": sig, "ids": deck_ids, "total": total}
        await save_discover_deck(redis_module.redis_client, viewer_id, sig, deck_ids, total)

    deck_ids = list(deck.get("ids", []))
    total = int(deck.get("total", len(deck_ids)))
    # Position of the requested page inside the deck.
    start: int | None
    if use_cursor:
        try:
            start = deck_ids.index(str(cursor_id)) + 1
        except ValueError:
            start = None  # cursor anchor fell out of the deck → DB fallback
    else:
        start = offset
    if start is not None and start >= len(deck_ids):
        if start >= total:
            # Past the end of the candidate set — empty final page.
            return DiscoverResponse(users=[], next_offset=None, next_cursor=None, total=total)
        start = None  # deep page beyond the cached cap → DB fallback

    has_next = False
    next_cursor = None
    next_offset = None
    if start is not None:
        # ── Serve the page from the deck ─────────────────────────────────────
        page_ids = deck_ids[start : start + limit]
        has_next = (start + limit) < total
        if has_next and not use_cursor:
            next_offset = start + limit
        result = await session.execute(
            select(User)
            .options(
                selectinload(User.profile),
                selectinload(User.settings),
                selectinload(User.photos),
            )
            .where(User.id.in_([UUID(p) for p in page_ids]))
        )
        by_id = {str(u.id): u for u in result.scalars().all()}
        rows: list = []
        for pid in page_ids:
            user = by_id.get(pid)
            if user is None or not user.profile:
                continue  # deleted/hidden between refill and fetch
            distance = None
            if has_coords and user.profile.lat is not None and user.profile.lng is not None:
                distance = haversine_km(
                    current_profile.lat, current_profile.lng, user.profile.lat, user.profile.lng
                )
            rows.append((user, distance))
    else:
        # ── DB fallback: cursor anchor missing or deep page beyond the cap ──
        query = (
            select(User)
            .options(
                selectinload(User.profile),
                selectinload(User.settings),
                selectinload(User.photos),
            )
            .join(UserProfile, User.id == UserProfile.user_id)
            .where(*conditions)
        )
        if has_coords and distance_expr is not None:
            query = query.add_columns(distance_expr.label("distance_km"))
        else:
            query = query.add_columns(func.cast(None, Float).label("distance_km"))
        query = query.order_by(*order)
        if use_cursor:
            if cursor_key is None:
                # Last row was in the null tail — every remaining row is also null.
                query = query.where(col.is_(None), User.id < cursor_id)
            else:
                query = query.where(
                    or_(
                        col < cursor_key,
                        and_(col == cursor_key, User.id < cursor_id),
                    )
                )
            # Fetch one extra row to detect whether another page exists.
            query = query.limit(limit + 1)
        else:
            query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        rows = list(result.all())
        if use_cursor:
            if len(rows) > limit:
                rows = rows[:limit]
                has_next = True
        else:
            has_next = offset + limit < total
            if has_next:
                next_offset = offset + limit

    # Always derive next_cursor from the last returned row whenever more rows
    # exist, even on an offset-based first request, so the client only needs to
    # follow next_cursor to page through the deck stably.
    if has_next and rows:
        last_user = rows[-1][0]
        key = last_user.last_seen_at.isoformat() if last_user.last_seen_at else None
        next_cursor = _encode_cursor(key, last_user.id)

    response_users = []

    # Batch query Redis for real-time online presence
    user_ids = [row[0].id for row in rows if row[0].profile]
    online_map = {}
    if user_ids:
        pipe = redis_module.redis_client.pipeline()
        for uid in user_ids:
            pipe.exists(f"online:{uid}")
        results = await pipe.execute()
        online_map = {str(uid): bool(r) for uid, r in zip(user_ids, results)}

    for row in rows:
        user = row[0]
        distance = row[1]
        if not user.profile:
            continue

        is_online = online_map.get(str(user.id), False)

        response_users.append(
            await serialize_card(
                user,
                is_online=is_online,
                distance_km=fuzz_distance(distance),
                is_verified=user.profile.is_verified
                if user.profile.is_verified is not None
                else False,
            )
        )

    return DiscoverResponse(
        users=response_users,
        next_offset=next_offset,
        next_cursor=next_cursor,
        total=total,
    )
