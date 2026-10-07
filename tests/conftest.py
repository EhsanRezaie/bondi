# tests/conftest.py
import os
import sys
import asyncio
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy import text, bindparam
import redis.asyncio as aioredis
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta
import uuid
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
_env_test = ".env.test" if os.path.exists(".env.test") else ".env.test.example"
load_dotenv(_env_test, override=True)

# ---------------------------------------------------------------------------
# Per-xdist-worker isolation. WORKER_ID drives an isolated Postgres database
# (bondi_test_<worker>) AND an isolated Redis logical DB. The Redis URL is
# rewritten BEFORE the app is imported so that modules doing
# `from app.core.redis import redis_client` also bind to the worker's DB
# (patching the module attribute alone misses those direct imports).
# ---------------------------------------------------------------------------
WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "master")
_WORKER_NUM = 0 if WORKER_ID == "master" else int(WORKER_ID.replace("gw", "") or 0)

ORIGINAL_DATABASE_URL = os.environ["DATABASE_URL"]


def _url_with_path(url: str, path: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


# Point BOTH the app's global engine and the test fixtures at the same worker
# database. Some code paths (e.g. background/backfill work) open their own
# session from settings instead of the overridden request session, so the app
# must not fall back to the shared master DB.
_parts = urlsplit(ORIGINAL_DATABASE_URL)
_base_db = _parts.path.lstrip("/")
_worker_db = _base_db if WORKER_ID == "master" else f"{_base_db}_{WORKER_ID}"
os.environ["DATABASE_URL"] = _url_with_path(ORIGINAL_DATABASE_URL, f"/{_worker_db}")
os.environ["REDIS_URL"] = _url_with_path(os.environ["REDIS_URL"], f"/{_WORKER_NUM}")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.main import app
from app.db.base import Base
from app.db.session import get_session
import app.core.redis as redis_module
from app.core.limiter import limiter
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings

# Keep the master URL for admin DB ops (CREATE/DROP the per-worker DB).
BASE_DATABASE_URL = ORIGINAL_DATABASE_URL
TEST_REDIS_URL = os.environ["REDIS_URL"]

# ---------------------------------------------------------------------------
# Per-xdist-worker DB name → each worker gets isolated DB, no cross-worker
# deadlocks on shared teardown DELETEs.
# ---------------------------------------------------------------------------


def _split_url(url: str):
    parts = urlsplit(url)
    base_db_name = parts.path.lstrip("/")
    return parts, base_db_name


def _worker_db_name() -> str:
    _, base_db_name = _split_url(BASE_DATABASE_URL)
    if WORKER_ID == "master":
        return base_db_name
    return f"{base_db_name}_{WORKER_ID}"


def _worker_db_url() -> str:
    parts, _ = _split_url(BASE_DATABASE_URL)
    new_path = f"/{_worker_db_name()}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def _admin_db_url() -> str:
    """URL pointing at 'postgres' maintenance DB, for CREATE/DROP DATABASE."""
    parts, _ = _split_url(BASE_DATABASE_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


TEST_DATABASE_URL = _worker_db_url()


def make_engine():
    return create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, echo=False)


def make_redis():
    # TEST_REDIS_URL already carries this worker's logical DB index (set above,
    # before the app import), so both the patched client and direct imports of
    # app.core.redis.redis_client land on the same isolated DB.
    return aioredis.from_url(TEST_REDIS_URL, encoding="utf-8", decode_responses=True)


async def _create_worker_database():
    if WORKER_ID == "master":
        return
    admin_engine = create_async_engine(
        _admin_db_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    async with admin_engine.connect() as conn:
        db_name = _worker_db_name()
        exists = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        if not exists.scalar():
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()


async def _drop_worker_database():
    if WORKER_ID == "master":
        return
    admin_engine = create_async_engine(
        _admin_db_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    async with admin_engine.connect() as conn:
        db_name = _worker_db_name()
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": db_name},
        )
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    await admin_engine.dispose()


# ---------------------------------------------------------------------------
# Seed interests into the database
# ---------------------------------------------------------------------------

async def seed_interests(conn):
    """Seed interests from JSON file into the database."""
    json_path = Path(__file__).parent.parent / "app" / "db" / "seed_data" / "interests.json"

    if not json_path.exists():
        print(f"⚠️ Interests file not found: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        interests_data = json.load(f)

    # First, delete all existing interests (clean slate)
    await conn.execute(text("DELETE FROM interests"))

    for item in interests_data:
        await conn.execute(
            text("""
                INSERT INTO interests (id, name, category, icon, translations)
                VALUES (gen_random_uuid(), :name, :category, :icon, :translations)
            """),
            {
                "name": item["name"],
                "category": item["category"],
                "icon": item.get("icon"),
                "translations": json.dumps(item.get("translations")),
            }
        )

    print(f"✅ Seeded {len(interests_data)} interests")


# ---------------------------------------------------------------------------
# Create tables once at session start, drop at session end
# ---------------------------------------------------------------------------

# Tables that hold static/seed data or the admin account. These are never
# truncated per-test, so we don't have to re-seed 158 interests every test.
PRESERVED_TABLES = {"users", "user_profiles", "user_settings", "interests"}

# Seed interest names (static reference data). reset_state deletes any interest
# NOT in this set so tests can insert their own rows without polluting others.
SEED_INTERESTS_PATH = Path(__file__).parent.parent / "app" / "db" / "seed_data" / "interests.json"


def _load_seed_interest_names() -> list:
    try:
        with open(SEED_INTERESTS_PATH, encoding="utf-8") as f:
            return [item["name"] for item in json.load(f)]
    except FileNotFoundError:
        return []


SEED_INTEREST_NAMES = _load_seed_interest_names()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """One engine for the whole session (NullPool → no loop-bound connections)."""
    engine = make_engine()
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def redis_client():
    """One Redis client for the whole session; flushed once per test."""
    r = make_redis()
    yield r
    await r.aclose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database(db_engine):
    await _create_worker_database()

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

        # ✅ Seed interests once per session (reset_state preserves them)
        await seed_interests(conn)

        # Create admin user for tests
        admin_id = uuid.uuid4()

        # Insert into users table
        await conn.execute(
            text("""
                INSERT INTO users (id, phone, phone_verified, is_active, token_version, registration_status, created_at)
                VALUES (
                    :id,
                    '+989100000000',
                    true,
                    true,
                    1,
                    'onboarding_complete',
                    NOW()
                )
            """),
            {"id": admin_id}
        )

        # Insert into user_profiles table
        await conn.execute(
            text("""
                INSERT INTO user_profiles (id, user_id, name, birth_date, gender, is_verified, created_at, updated_at)
                VALUES (
                    :id,
                    :user_id,
                    'Test Admin',
                    '1990-01-01',
                    'male',
                    true,
                    NOW(),
                    NOW()
                )
            """),
            {"id": uuid.uuid4(), "user_id": admin_id}
        )

        # Insert into user_settings table
        await conn.execute(
            text("""
                INSERT INTO user_settings (id, user_id, hide_last_seen, hide_online_status, created_at, updated_at)
                VALUES (
                    :id,
                    :user_id,
                    false,
                    false,
                    NOW(),
                    NOW()
                )
            """),
            {"id": uuid.uuid4(), "user_id": admin_id}
        )

    yield
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _drop_worker_database()


# ---------------------------------------------------------------------------
# Per-test: truncate all tables + flush Redis
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def reset_state(db_engine):
    yield
    # Wipe every table except the preserved ones in a single statement. This
    # replaces ~22 round-trips of individual DELETEs + a 158-row interest
    # re-seed per test. Redis is flushed once per test in patch_redis.
    names = [t.name for t in Base.metadata.sorted_tables if t.name not in PRESERVED_TABLES]
    quoted = ", ".join(f'"{n}"' for n in names)
    async with db_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        # Remove interests inserted by tests, keeping the seeded 158 in place.
        if SEED_INTEREST_NAMES:
            await conn.execute(
                text("DELETE FROM interests WHERE name NOT IN :names")
                .bindparams(bindparam("names", expanding=True)),
                {"names": SEED_INTEREST_NAMES},
            )
        # Delete non-admin users and their related rows.
        await conn.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE phone != '+989100000000')"))
        await conn.execute(text("DELETE FROM user_settings WHERE user_id IN (SELECT id FROM users WHERE phone != '+989100000000')"))
        await conn.execute(text("DELETE FROM users WHERE phone != '+989100000000'"))


# ---------------------------------------------------------------------------
# Per-test DB session
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Per-test Redis — patches the app's redis_client for this test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def patch_redis(redis_client):
    await redis_client.flushdb()
    original = redis_module.redis_client
    redis_module.redis_client = redis_client
    yield redis_client
    redis_module.redis_client = original


# ---------------------------------------------------------------------------
# Disable rate limiting for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
def disable_rate_limiting():
    original_enabled = getattr(limiter, "enabled", True)
    limiter.enabled = False
    yield
    limiter.enabled = original_enabled


# ---------------------------------------------------------------------------
# Mock WebSocket manager for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
def mock_websocket_manager():
    async def _online_bulk(user_ids, redis=None):
        pipe = redis_module.redis_client.pipeline()
        for uid in user_ids:
            pipe.exists(f"online:{uid}")
        results = await pipe.execute()
        return {uid: bool(r) for uid, r in zip(user_ids, results)}

    with (patch("app.api.v1.endpoints.swipes.websocket_manager") as mock,
          patch("app.api.v1.endpoints.messages.websocket_manager") as mock_msgs,
          patch("app.api.v1.endpoints.chats.websocket_manager") as mock_chats,
          patch("app.api.v1.endpoints.blocks.websocket_manager") as mock_blocks):
        for m in (mock, mock_msgs, mock_chats, mock_blocks):
            m.broadcast_match = AsyncMock()
            m.send_to_match = AsyncMock()
            m.send_to_conversation = AsyncMock()
            m.send_personal_message = AsyncMock()
            m.get_online_status_bulk = AsyncMock(side_effect=_online_bulk)
        yield mock


# ---------------------------------------------------------------------------
# Mock SMS service for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
def mock_sms_service():
    """Mock the SMS service to avoid real SMS calls in tests."""
    with patch("app.api.v1.endpoints.auth.send_verification_code", new_callable=AsyncMock) as mock_send:
        yield mock_send


# ---------------------------------------------------------------------------
# Mock Redis verification code for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def mock_verification_code():
    """Helper fixture to store a verification code in Redis for testing."""
    async def _store_code(phone: str, code: str = "123456"):
        r = redis_module.redis_client
        await r.setex(f"verification:{phone}", 300, json.dumps({"code": code, "attempts": 0}))
        return code
    return _store_code


@pytest_asyncio.fixture
async def mock_delete_code():
    """Helper fixture to store a delete-account confirmation code in Redis."""
    async def _store_code(user_id: str, code: str = "123456"):
        r = redis_module.redis_client
        await r.setex(f"delete_verify:{user_id}", 300, json.dumps({"code": code, "attempts": 0}))
        return code
    return _store_code


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()

@pytest_asyncio.fixture
def admin_headers() -> dict:
    """Create admin auth headers."""
    from app.core.config import settings
    return {"X-Admin-Key": settings.ADMIN_SECRET_KEY}