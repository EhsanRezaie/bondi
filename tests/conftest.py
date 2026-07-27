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

from dotenv import load_dotenv
load_dotenv(".env.test", override=True)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.main import app
from app.db.base import Base
from app.db.session import get_session
import app.core.redis as redis_module
from app.core.limiter import limiter
from app.core.security import hash_password
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
TEST_REDIS_URL = os.environ["REDIS_URL"]


def make_engine():
    return create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, echo=False)


def make_redis():
    return aioredis.from_url(TEST_REDIS_URL, encoding="utf-8", decode_responses=True)


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
                INSERT INTO interests (id, name, category, icon)
                VALUES (gen_random_uuid(), :name, :category, :icon)
            """),
            {
                "name": item["name"],
                "category": item["category"],
                "icon": item.get("icon"),
            }
        )
    
    print(f"✅ Seeded {len(interests_data)} interests")


# ---------------------------------------------------------------------------
# Create tables once at session start, drop at session end
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
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
                INSERT INTO users (id, email, password_hash, is_active, token_version, registration_status, created_at)
                VALUES (
                    :id,
                    'admin@test.com',
                    :password,
                    true,
                    1,
                    'onboarding_complete',
                    NOW()
                )
            """),
            {"id": admin_id, "password": hash_password("admin123")}
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
        await conn.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE email != 'admin@test.com')"))
        await conn.execute(text("DELETE FROM user_settings WHERE user_id IN (SELECT id FROM users WHERE email != 'admin@test.com')"))
        await conn.execute(text("DELETE FROM users WHERE email != 'admin@test.com'"))
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
    original_enabled = getattr(limiter, "_enabled", True)
    limiter._enabled = False
    yield
    limiter._enabled = original_enabled


# ---------------------------------------------------------------------------
# Mock WebSocket manager for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
def mock_websocket_manager():
    with patch("app.api.v1.endpoints.swipes.websocket_manager") as mock:
        mock.broadcast_match = AsyncMock()
        mock.send_to_match = AsyncMock()
        yield mock


# ---------------------------------------------------------------------------
# Mock Email Service for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
def mock_email_service():
    """Mock email service to avoid sending real emails in tests."""
    with patch("app.services.email_service.send_verification_code", new_callable=AsyncMock) as mock_send:
        yield mock_send


# ---------------------------------------------------------------------------
# Mock Redis verification code for tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def mock_verification_code():
    """Helper fixture to store verification code in Redis for testing."""
    async def _store_code(email: str, code: str = "123456"):
        r = redis_module.redis_client
        await r.setex(f"verification:{email}", 300, code)
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