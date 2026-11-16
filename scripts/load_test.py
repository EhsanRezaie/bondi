# scripts/load_test.py
"""Async load-test harness for Bondi (Phase 8 of the pre-launch review).

Signs real JWTs for seeded users (no registration flow needed), fires
concurrent requests against a running app, and reports RPS + latency
percentiles + status-code histogram per scenario.

Usage (from repo root, against the test stack):
    set -a; . .env.test; set +a
    RATELIMIT_ENABLED=false ENVIRONMENT=development uvicorn app.main:app --port 8100 &
    .venv/bin/python scripts/load_test.py --base http://127.0.0.1:8100 \
        --scenario discover --concurrency 20 --seconds 30 --users 200

Requires the 60k-user synthetic volume (see plan Phase 4 seeding).
"""

import argparse
import asyncio
import statistics
import sys
import time
from collections import Counter

import httpx

sys.path.insert(0, ".")  # repo-root imports


def percentile(sorted_xs: list[float], p: float) -> float:
    if not sorted_xs:
        return 0.0
    k = min(len(sorted_xs) - 1, round(p / 100 * (len(sorted_xs) - 1)))
    return sorted_xs[k]


async def run_scenario(client: httpx.AsyncClient, args, builder) -> dict:
    latencies: list[float] = []
    codes: Counter = Counter()
    errors = 0
    first_bad_body = ""
    lock = asyncio.Lock()
    stop = asyncio.Event()

    async def worker(i: int):
        nonlocal errors, first_bad_body
        j = 0
        while not stop.is_set():
            kwargs = builder(i, j)
            if kwargs is None:  # scenario exhausted for this worker
                break
            t0 = time.perf_counter()
            try:
                r = await client.request(**kwargs)
                dt = time.perf_counter() - t0
                async with lock:
                    latencies.append(dt)
                    codes[r.status_code] += 1
                    if r.status_code != 200 and not first_bad_body:
                        first_bad_body = r.text[:200]
            except Exception:
                errors += 1
            j += 1

    workers = [asyncio.create_task(worker(i)) for i in range(args.concurrency)]
    await asyncio.sleep(args.seconds)
    stop.set()
    await asyncio.gather(*workers, return_exceptions=True)

    latencies.sort()
    total = len(latencies)
    return {
        "total": total,
        "rps": round(total / args.seconds, 1),
        "p50": round(percentile(latencies, 50) * 1000, 1),
        "p95": round(percentile(latencies, 95) * 1000, 1),
        "p99": round(percentile(latencies, 99) * 1000, 1),
        "max": round(latencies[-1] * 1000, 1) if latencies else 0.0,
        "mean": round(statistics.mean(latencies) * 1000, 1) if latencies else 0.0,
        "codes": dict(codes),
        "errors": errors,
        "first_bad": first_bad_body,
    }


class Ctx:
    """Shared per-run state handed to every scenario builder."""

    def __init__(self, tokens, targets, chats=None, refresh_pool=None, concurrency=1):
        self.tokens = tokens
        self.targets = targets
        self.chats = chats or {}  # worker_index -> chat_id that worker exercises
        self.refresh_pool = refresh_pool or []
        self.concurrency = concurrency


def build_discover(i: int, j: int, ctx: Ctx):
    token = ctx.tokens[i % len(ctx.tokens)]
    return {
        "method": "GET",
        "url": "/api/v1/discover",
        "headers": {"Authorization": f"Bearer {token}"},
        "params": {"limit": 20, "offset": (j * 7) % 400},
    }


def build_chats(i: int, j: int, ctx: Ctx):
    return {
        "method": "GET",
        "url": "/api/v1/chats",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
    }


def build_matches(i: int, j: int, ctx: Ctx):
    return {
        "method": "GET",
        "url": "/api/v1/matches",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
    }


def build_liked(i: int, j: int, ctx: Ctx):
    return {
        "method": "GET",
        "url": "/api/v1/swipes/liked",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
    }


def build_swipe(i: int, j: int, ctx: Ctx):
    # Each viewer likes a rotating pool of targets; duplicates expected → 400s
    # are part of the histogram (they exercise the dup path under load).
    token = ctx.tokens[i % len(ctx.tokens)]
    target = ctx.targets[(i + j) % len(ctx.targets)]
    return {
        "method": "POST",
        "url": "/api/v1/swipes",
        "headers": {"Authorization": f"Bearer {token}"},
        "json": {"user_id": target, "direction": "like"},
    }


def build_profile(i: int, j: int, ctx: Ctx):
    token = ctx.tokens[i % len(ctx.tokens)]
    target = ctx.targets[(i + j) % len(ctx.targets)]
    return {
        "method": "GET",
        "url": f"/api/v1/users/{target}",
        "headers": {"Authorization": f"Bearer {token}"},
    }


def build_notifs(i: int, j: int, ctx: Ctx):
    return {
        "method": "GET",
        "url": "/api/v1/notifications",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
    }


def build_search(i: int, j: int, ctx: Ctx):
    token = ctx.tokens[i % len(ctx.tokens)]
    return {
        "method": "GET",
        "url": "/api/v1/search",
        "headers": {"Authorization": f"Bearer {token}"},
        "params": {"limit": 20, "offset": (j * 7) % 400},
    }


def build_search_near(i: int, j: int, ctx: Ctx):
    # Distance-filtered variant — exercises the PostGIS ST_DWithin index path.
    token = ctx.tokens[i % len(ctx.tokens)]
    return {
        "method": "GET",
        "url": "/api/v1/search",
        "headers": {"Authorization": f"Bearer {token}"},
        "params": {"limit": 20, "offset": (j * 7) % 400, "distance_km": 25},
    }


def build_msgs(i: int, j: int, ctx: Ctx):
    # One dedicated chat per worker; paginate its history.
    chat_id = ctx.chats.get(i)
    if chat_id is None:
        return None  # worker has no chat — sit out
    return {
        "method": "GET",
        "url": f"/api/v1/messages/{chat_id}",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
        "params": {"limit": 30, "offset": (j * 20) % 200},
    }


def build_send(i: int, j: int, ctx: Ctx):
    chat_id = ctx.chats.get(i)
    if chat_id is None:
        return None
    return {
        "method": "POST",
        "url": f"/api/v1/messages/{chat_id}/text",
        "headers": {"Authorization": f"Bearer {ctx.tokens[i % len(ctx.tokens)]}"},
        "json": {"content": f"load-test message {i}-{j}"},
    }


def build_refresh(i: int, j: int, ctx: Ctx):
    # Refresh tokens are single-use (rotation) — worker i consumes slot i, i+C, i+2C…
    idx = i + j * ctx.concurrency
    if idx >= len(ctx.refresh_pool):
        return None  # pool exhausted — worker sits out
    return {
        "method": "POST",
        "url": "/api/v1/auth/refresh",
        "json": {"refresh_token": ctx.refresh_pool[idx]},
    }


SCENARIOS = {
    "discover": build_discover,
    "chats": build_chats,
    "matches": build_matches,
    "liked": build_liked,
    "swipes": build_swipe,
    "profile": build_profile,
    "notifs": build_notifs,
    "search": build_search,
    "search_near": build_search_near,
    "msgs": build_msgs,
    "send": build_send,
    "refresh": build_refresh,
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--users", type=int, default=50, help="distinct viewer tokens")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    from sqlalchemy import text

    from app.core.security import create_access_token, create_refresh_token
    from app.db.session import AsyncSessionLocal

    # Pick N distinct real user ids (viewers) + N targets (for swipes/profile).
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT id FROM users WHERE is_active ORDER BY id LIMIT :n"),
                {"n": args.users * 2},
            )
        ).all()
    ids = [str(r[0]) for r in rows]
    if len(ids) < args.users:
        raise SystemExit(f"need {args.users} users, DB has {len(ids)}")
    tokens = [create_access_token(uid, token_version=0) for uid in ids[: args.users]]
    targets = ids[args.users :]

    ctx = Ctx(tokens, targets, concurrency=args.concurrency)

    # Message scenarios: one real, active chat with messages per worker.
    if args.scenario in ("msgs", "send"):
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT u2.id, c.id FROM users u2 "
                        "JOIN LATERAL ("
                        "  SELECT id FROM chats"
                        "  WHERE (initiator_id = u2.id OR recipient_id = u2.id)"
                        "    AND is_active"
                        "    AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = chats.id)"
                        "  LIMIT 1"
                        ") c ON true "
                        "WHERE u2.id = ANY(:ids) ORDER BY u2.id"
                    ),
                    {"ids": ids},
                )
            ).all()
        chat_by_user = {str(u): str(c) for u, c in rows}
        # Map worker i → viewer ids[i % users] (same rotation builders use).
        ctx.chats = {
            i: chat_by_user[ids[i % args.users]]
            for i in range(args.concurrency)
            if ids[i % args.users] in chat_by_user
        }
        if not ctx.chats:
            raise SystemExit("no active chats with messages found for selected users")

    # Refresh scenario: pre-mint single-use tokens (each its own rotation family).
    if args.scenario == "refresh":
        from app.core import redis as redis_mod

        pool_size = min(args.concurrency * args.seconds, 5000)
        # Each call mints a unique JWT (random jti); rotate owners across users.
        ctx.refresh_pool = [
            create_refresh_token(ids[k % args.users], token_version=0) for k in range(pool_size)
        ]
        for tok, uid in zip(
            ctx.refresh_pool, [ids[k % args.users] for k in range(pool_size)], strict=True
        ):
            await redis_mod.store_refresh_token(tok, uid)

    build = SCENARIOS[args.scenario]
    async with httpx.AsyncClient(base_url=args.base, timeout=60.0) as client:
        result = await run_scenario(client, args, lambda i, j: build(i, j, ctx))

    name = args.scenario
    print(f"\n=== {name} | conc={args.concurrency} | {args.seconds}s ===")
    print(
        f"rps={result['rps']} total={result['total']} "
        f"p50={result['p50']}ms p95={result['p95']}ms p99={result['p99']}ms "
        f"max={result['max']}ms mean={result['mean']}ms"
    )
    print(f"status={result['codes']} errors={result['errors']}")
    if result["first_bad"]:
        print(f"first non-200 body: {result['first_bad']}")


if __name__ == "__main__":
    asyncio.run(main())
