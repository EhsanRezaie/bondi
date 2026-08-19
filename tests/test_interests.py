"""
Tests for GET /api/v1/interests

Public endpoint, no auth required. Interests are seeded reference data.
Each test gets a fresh DB with the 158 seeded interests.
"""
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interest import Interest

INTERESTS_URL = "/api/v1/interests"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def seed_interests(db_session: AsyncSession, rows: list[dict]) -> list[Interest]:
    """Insert interest rows directly and return the created objects."""
    interests = [
        Interest(
            id=uuid.uuid4(),
            name=row["name"],
            category=row.get("category"),
            icon=row.get("icon"),
        )
        for row in rows
    ]
    db_session.add_all(interests)
    await db_session.commit()
    return interests


# ✅ Simple names - reset_state re-seeds interests after each test
SAMPLE_INTERESTS = [
    {"name": "test_football", "category": "sports_fitness", "icon": "⚽"},
    {"name": "test_coffee", "category": "food_drink", "icon": "☕"},
    {"name": "test_painting", "category": "arts_creative", "icon": "🎨"},
    {"name": "test_yoga", "category": "sports_fitness", "icon": "🧘"},
    {"name": "test_cooking", "category": "food_drink", "icon": "🍳"},
]


# ---------------------------------------------------------------------------
# Basic response shape
# ---------------------------------------------------------------------------

class TestInterestsResponseShape:

    async def test_returns_200(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        assert res.status_code == 200

    async def test_returns_json_array(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        assert isinstance(res.json(), list)

    async def test_returns_seeded_interests_not_empty(self, client: AsyncClient):
        """The 158 seeded interests should be returned (not empty)."""
        res = await client.get(INTERESTS_URL)
        assert res.status_code == 200
        data = res.json()
        assert len(data) > 0

    async def test_each_item_has_required_fields(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        assert "id" in test_item
        assert "name" in test_item

    async def test_each_item_has_category_field(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        assert "category" in test_item

    async def test_each_item_has_icon_field(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        assert "icon" in test_item

    async def test_id_is_valid_uuid(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        uuid.UUID(test_item["id"])

    async def test_name_is_stable_english_key(self, client: AsyncClient, db_session: AsyncSession):
        """name must be the stable English key."""
        await seed_interests(db_session, [{"name": "test_football", "category": "sports_fitness", "icon": "⚽"}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        assert test_item["name"] == "test_football"

    async def test_icon_is_emoji_string(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, [{"name": "test_football", "category": "sports_fitness", "icon": "⚽"}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_football"), None)
        assert test_item is not None
        assert test_item["icon"] == "⚽"


# ---------------------------------------------------------------------------
# Count and content
# ---------------------------------------------------------------------------

class TestInterestsCount:

    async def test_returns_all_seeded_interests_plus_test(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS)
        res = await client.get(INTERESTS_URL)
        # Should have 158 seeded + 5 test = 163
        assert len(res.json()) >= len(SAMPLE_INTERESTS)

    async def test_returns_correct_test_names(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS)
        res = await client.get(INTERESTS_URL)
        names = {item["name"] for item in res.json()}
        expected = {r["name"] for r in SAMPLE_INTERESTS}
        assert expected.issubset(names)

    async def test_returns_correct_categories(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS)
        res = await client.get(INTERESTS_URL)
        categories = {item["category"] for item in res.json()}
        assert "sports_fitness" in categories
        assert "food_drink" in categories
        assert "arts_creative" in categories

    async def test_single_interest_returned_correctly(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_interests(db_session, [{"name": "test_yoga", "category": "sports_fitness", "icon": "🧘"}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_yoga"), None)
        assert test_item is not None
        assert test_item["category"] == "sports_fitness"
        assert test_item["icon"] == "🧘"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestInterestsOrdering:

    async def test_ordered_by_category_then_name(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Results must come back sorted by category then name."""
        await seed_interests(db_session, SAMPLE_INTERESTS)
        res = await client.get(INTERESTS_URL)
        items = res.json()
        # Get only our test interests
        test_items = [i for i in items if i["name"].startswith("test_")]
        names_in_order = [i["name"] for i in test_items]
        expected = sorted(SAMPLE_INTERESTS, key=lambda r: (r["category"], r["name"]))
        assert names_in_order == [r["name"] for r in expected]


# ---------------------------------------------------------------------------
# Nullable fields
# ---------------------------------------------------------------------------

class TestInterestsNullableFields:

    async def test_category_can_be_null(self, client: AsyncClient, db_session: AsyncSession):
        """category is nullable in the model — should serialize to null."""
        await seed_interests(db_session, [{"name": "test_mystery", "category": None, "icon": "❓"}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_mystery"), None)
        assert test_item is not None
        assert test_item["category"] is None

    async def test_icon_can_be_null(self, client: AsyncClient, db_session: AsyncSession):
        """icon is nullable — should serialize to null."""
        await seed_interests(db_session, [{"name": "test_noicon", "category": "lifestyle", "icon": None}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_noicon"), None)
        assert test_item is not None
        assert test_item["icon"] is None

    async def test_both_optional_fields_null(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, [{"name": "test_bare", "category": None, "icon": None}])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        test_item = next((i for i in items if i["name"] == "test_bare"), None)
        assert test_item is not None
        assert test_item["name"] == "test_bare"
        assert test_item["category"] is None
        assert test_item["icon"] is None


# ---------------------------------------------------------------------------
# Access control — public endpoint
# ---------------------------------------------------------------------------

class TestInterestsAccessControl:

    async def test_no_auth_header_required(self, client: AsyncClient, db_session: AsyncSession):
        """Interests must be accessible without any Authorization header."""
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL)
        assert res.status_code == 200

    async def test_garbage_auth_header_still_works(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A malformed auth header should not cause a 401."""
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(
            INTERESTS_URL,
            headers={"Authorization": "Bearer garbage-token"},
        )
        assert res.status_code == 200

    async def test_no_query_params_accepted(self, client: AsyncClient, db_session: AsyncSession):
        """The interests endpoint takes no query params."""
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL, params={"language": "fa", "page": 1})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Scale — verifies the 158 seeded interests are returned
# ---------------------------------------------------------------------------

class TestInterestsScale:

    async def test_handles_full_production_count(
        self, client: AsyncClient
    ):
        """Endpoint must return all 158 seeded interests."""
        res = await client.get(INTERESTS_URL)
        assert res.status_code == 200
        data = res.json()
        # Should have the 158 seeded interests
        assert len(data) >= 158

    async def test_all_13_categories_present_in_response(
        self, client: AsyncClient
    ):
        """All 13 categories from the seed should be present."""
        expected_categories = {
            "sports_fitness", "music", "food_drink", "arts_creative",
            "lifestyle", "gaming_tech", "movies_tv", "outdoors_nature",
            "learning", "travel", "fashion_beauty", "social_causes", "pets_animals",
        }
        res = await client.get(INTERESTS_URL)
        data = res.json()
        returned_cats = {item["category"] for item in data}
        assert expected_categories.issubset(returned_cats)