# tests/conftest.py
import os
import sys
import asyncio
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy import text
import redis.asyncio as aioredis
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta
import uuid
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
load_dotenv(".env.test", override=True)

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

BASE_DATABASE_URL = os.environ["DATABASE_URL"]
TEST_REDIS_URL = os.environ["REDIS_URL"]

# ---------------------------------------------------------------------------
# Per-xdist-worker DB name → each worker gets isolated DB, no cross-worker
# deadlocks on shared teardown DELETEs.
# ---------------------------------------------------------------------------

WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "master")


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
    # Separate Redis DB index per worker too, avoid flushdb races across workers.
    worker_num = 0 if WORKER_ID == "master" else int(WORKER_ID.replace("gw", "") or 0)
    url = TEST_REDIS_URL
    if "/0" in url or url.rstrip("/").split("/")[-1].isdigit():
        base = url.rsplit("/", 1)[0]
        url = f"{base}/{worker_num}"
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)


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

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    await _create_worker_database()

    engine = make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

        # ✅ Seed interests after tables are created
        await seed_interests(conn)

        # Create admin user for tests
        admin_id = uuid.uuid4()

        # Insert into users table
        await conn.execute(
            text("""
                INSERT INTO users (id, phone, email, phone_verified, is_active, token_version, registration_status, created_at)
                VALUES (
                    :id,
                    '+989100000000',
                    'admin@test.com',
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

    await engine.dispose()
    yield
    engine = make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    await _drop_worker_database()


# ---------------------------------------------------------------------------
# Per-test: truncate all tables + flush Redis
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    yield
    engine = make_engine()
    async with engine.begin() as conn:
        # Delete all tables except preserve admin user
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in ['users', 'user_profiles', 'user_settings']:
                await conn.execute(table.delete())

        # ✅ Re-seed interests after deletion
        await seed_interests(conn)

        # Delete non-admin users and their related data
        await conn.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE phone != '+989100000000')"))
        await conn.execute(text("DELETE FROM user_settings WHERE user_id IN (SELECT id FROM users WHERE phone != '+989100000000')"))
        await conn.execute(text("DELETE FROM users WHERE phone != '+989100000000'"))
    await engine.dispose()
    r = make_redis()
    await r.flushdb()
    await r.aclose()


# ---------------------------------------------------------------------------
# Per-test DB session
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    engine = make_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test Redis — patches the app's redis_client for this test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def patch_redis():
    r = make_redis()
    await r.flushdb()
    original = redis_module.redis_client
    redis_module.redis_client = r
    yield r
    redis_module.redis_client = original
    await r.aclose()


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