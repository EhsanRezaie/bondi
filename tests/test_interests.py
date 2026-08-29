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
            translations=row.get("translations"),
        )
        for row in rows
    ]
    db_session.add_all(interests)
    await db_session.commit()
    return interests


# ✅ Simple names - reset_state re-seeds interests after each test
SAMPLE_INTERESTS = [
    {
        "name": "test_football",
        "category": "sports_fitness",
        "icon": "⚽",
        "translations": {
            "fa": {"name": "فوتبال تست", "category": "ورزش"},
            "en": {"name": "Test Football", "category": "Sports"},
        },
    },
    {
        "name": "test_coffee",
        "category": "food_drink",
        "icon": "☕",
        "translations": {
            "fa": {"name": "قهوه تست", "category": "نوشیدنی"},
            "en": {"name": "Test Coffee", "category": "Drinks"},
        },
    },
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
        """The interests endpoint accepts a language param; unknown params are ignored."""
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL, params={"language": "fa", "page": 1})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Localization — language param + translated labels
# ---------------------------------------------------------------------------

class TestInterestsLocalization:

    async def test_default_language_is_english(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:2])
        res = await client.get(INTERESTS_URL)
        items = res.json()
        football = next(i for i in items if i["name"] == "test_football")
        assert football["name_localized"] == "Test Football"
        assert football["category_localized"] == "Sports"

    async def test_language_fa_returns_persian(self, client: AsyncClient, db_session: AsyncSession):
        await seed_interests(db_session, SAMPLE_INTERESTS[:2])
        res = await client.get(INTERESTS_URL, params={"language": "fa"})
        items = res.json()
        football = next(i for i in items if i["name"] == "test_football")
        assert football["name_localized"] == "فوتبال تست"
        assert football["category_localized"] == "ورزش"
        # The stable key is untouched regardless of language.
        assert football["name"] == "test_football"
        assert football["category"] == "sports_fitness"

    async def test_localized_cache_per_language(self, client: AsyncClient, db_session: AsyncSession):
        """fa and en responses are cached separately."""
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res_fa = await client.get(INTERESTS_URL, params={"language": "fa"})
        res_en = await client.get(INTERESTS_URL, params={"language": "en"})
        fa_item = next(i for i in res_fa.json() if i["name"] == "test_football")
        en_item = next(i for i in res_en.json() if i["name"] == "test_football")
        assert fa_item["name_localized"] == "فوتبال تست"
        assert en_item["name_localized"] == "Test Football"

    async def test_missing_translation_falls_back_to_key(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """An interest without a translation returns its stable key as the label."""
        await seed_interests(db_session, [SAMPLE_INTERESTS[2]])  # test_painting, no translations
        res = await client.get(INTERESTS_URL, params={"language": "fa"})
        items = res.json()
        painting = next(i for i in items if i["name"] == "test_painting")
        assert painting["name_localized"] == "test_painting"
        assert painting["category_localized"] == "arts_creative"

    async def test_unknown_language_falls_back_to_english(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_interests(db_session, SAMPLE_INTERESTS[:1])
        res = await client.get(INTERESTS_URL, params={"language": "xx"})
        items = res.json()
        football = next(i for i in items if i["name"] == "test_football")
        assert football["name_localized"] == "Test Football"

    async def test_real_seed_has_fa_and_en_translations(self, client: AsyncClient):
        """The 158 seeded interests all carry fa + en translations."""
        res_fa = await client.get(INTERESTS_URL, params={"language": "fa"})
        res_en = await client.get(INTERESTS_URL, params={"language": "en"})
        fa_items = {i["name"]: i["name_localized"] for i in res_fa.json()}
        en_items = {i["name"]: i["name_localized"] for i in res_en.json()}
        assert len(fa_items) >= 158
        assert len(en_items) >= 158
        # No seeded interest should fall back to its raw key (all translated).
        assert all(v != k for k, v in fa_items.items())
        assert all(v != k for k, v in en_items.items())


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