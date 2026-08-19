"""
Tests for GET /api/v1/prompts

Public endpoint, no auth required. Supports ?language=fa (default) and
?language=en. Questions are stored per-language in the DB — the same
logical prompt exists as separate rows for each language.

Each test seeds its own rows via db_session since conftest.reset_state
wipes the prompts table after every test.
"""
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt import Prompt

PROMPTS_URL = "/api/v1/prompts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def seed_prompts(db_session: AsyncSession, rows: list[dict]) -> list[Prompt]:
    """Insert prompt rows directly and return the created objects."""
    prompts = [
        Prompt(
            id=uuid.uuid4(),
            question=row["question"],
            category=row.get("category"),
            language=row.get("language", "fa"),
            is_active=row.get("is_active", True),
        )
        for row in rows
    ]
    db_session.add_all(prompts)
    await db_session.commit()
    return prompts


FA_PROMPTS = [
    {"question": "دو تا راست یکی دروغ درباره‌ام...", "category": "about_me", "language": "fa"},
    {"question": "دوستام میگن من آدمی هستم که...", "category": "about_me", "language": "fa"},
    {"question": "یه یکشنبه‌ی معمولیم اینجوریه که...", "category": "lifestyle", "language": "fa"},
    {"question": "دنبال کسی‌ام که...", "category": "relationships", "language": "fa"},
    {"question": "بدترین هدیه‌ای که گرفتم...", "category": "fun", "language": "fa"},
]

EN_PROMPTS = [
    {"question": "Two truths and a lie about me are...", "category": "about_me", "language": "en"},
    {"question": "My friends would describe me as...", "category": "about_me", "language": "en"},
    {"question": "A typical Sunday for me looks like...", "category": "lifestyle", "language": "en"},
    {"question": "I'm looking for someone who...", "category": "relationships", "language": "en"},
    {"question": "Worst gift I ever received...", "category": "fun", "language": "en"},
]

BOTH_LANGUAGES = FA_PROMPTS + EN_PROMPTS


# ---------------------------------------------------------------------------
# Basic response shape
# ---------------------------------------------------------------------------

class TestPromptsResponseShape:

    async def test_returns_200(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS)
        res = await client.get(PROMPTS_URL)
        assert res.status_code == 200

    async def test_returns_json_array(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS)
        res = await client.get(PROMPTS_URL)
        assert isinstance(res.json(), list)

    async def test_empty_when_no_prompts_seeded(self, client: AsyncClient):
        """Empty DB returns empty list, not error."""
        res = await client.get(PROMPTS_URL)
        assert res.status_code == 200
        assert res.json() == []

    async def test_each_item_has_id(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert "id" in item

    async def test_each_item_has_question(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert "question" in item
        assert len(item["question"]) > 0

    async def test_each_item_has_category(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert "category" in item

    async def test_each_item_has_language(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert "language" in item

    async def test_id_is_valid_uuid(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        uuid.UUID(item["id"])

    async def test_question_is_real_display_text(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Unlike interests (which use English keys), prompt questions ARE
        the actual display text already — no client-side translation."""
        await seed_prompts(db_session, [FA_PROMPTS[0]])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert item["question"] == "دو تا راست یکی دروغ درباره‌ام..."


# ---------------------------------------------------------------------------
# Language filtering
# ---------------------------------------------------------------------------

class TestPromptsLanguageFilter:

    async def test_defaults_to_fa(self, client: AsyncClient, db_session: AsyncSession):
        """?language param defaults to 'fa' — no explicit param = Persian."""
        await seed_prompts(db_session, BOTH_LANGUAGES)
        res = await client.get(PROMPTS_URL)
        items = res.json()
        assert len(items) > 0
        assert all(item["language"] == "fa" for item in items)

    async def test_explicit_fa_returns_only_persian(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_prompts(db_session, BOTH_LANGUAGES)
        res = await client.get(PROMPTS_URL, params={"language": "fa"})
        items = res.json()
        assert len(items) > 0
        assert all(item["language"] == "fa" for item in items)

    async def test_en_returns_only_english(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, BOTH_LANGUAGES)
        res = await client.get(PROMPTS_URL, params={"language": "en"})
        items = res.json()
        assert len(items) > 0
        assert all(item["language"] == "en" for item in items)

    async def test_fa_and_en_counts_match(self, client: AsyncClient, db_session: AsyncSession):
        """Both languages should have the same number of prompts — they're
        seeded 1:1 paired."""
        await seed_prompts(db_session, BOTH_LANGUAGES)
        fa_count = len((await client.get(PROMPTS_URL, params={"language": "fa"})).json())
        en_count = len((await client.get(PROMPTS_URL, params={"language": "en"})).json())
        assert fa_count == en_count == len(FA_PROMPTS)

    async def test_unknown_language_returns_empty_list(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """An unsupported language code (e.g. 'de') should return an empty
        list, not a 422 or 404 — the endpoint doesn't validate language
        codes against an enum, it just queries and returns what exists."""
        await seed_prompts(db_session, BOTH_LANGUAGES)
        res = await client.get(PROMPTS_URL, params={"language": "de"})
        assert res.status_code == 200
        assert res.json() == []

    async def test_language_param_too_short_rejected(self, client: AsyncClient):
        """language param has min_length=2 — a single character is invalid."""
        res = await client.get(PROMPTS_URL, params={"language": "f"})
        assert res.status_code == 422

    async def test_language_param_too_long_rejected(self, client: AsyncClient):
        """language param has max_length=5 — 'persian' (7 chars) is invalid."""
        res = await client.get(PROMPTS_URL, params={"language": "persian"})
        assert res.status_code == 422

    async def test_fa_response_contains_persian_text(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Smoke-test that the actual Persian text round-trips correctly
        through Postgres and the JSON serializer without corruption."""
        fa_question = "دو تا راست یکی دروغ درباره‌ام..."
        await seed_prompts(db_session, [
            {"question": fa_question, "category": "about_me", "language": "fa"}
        ])
        res = await client.get(PROMPTS_URL, params={"language": "fa"})
        questions = [item["question"] for item in res.json()]
        assert fa_question in questions

    async def test_en_response_contains_english_text(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_prompts(db_session, [
            {"question": "Two truths and a lie about me are...", "category": "about_me", "language": "en"}
        ])
        res = await client.get(PROMPTS_URL, params={"language": "en"})
        questions = [item["question"] for item in res.json()]
        assert "Two truths and a lie about me are..." in questions

    async def test_same_category_exists_in_both_languages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Each category should be present in both language responses."""
        await seed_prompts(db_session, BOTH_LANGUAGES)
        fa_cats = {i["category"] for i in (await client.get(PROMPTS_URL, params={"language": "fa"})).json()}
        en_cats = {i["category"] for i in (await client.get(PROMPTS_URL, params={"language": "en"})).json()}
        assert fa_cats == en_cats


# ---------------------------------------------------------------------------
# is_active filtering
# ---------------------------------------------------------------------------

class TestPromptsActiveFilter:

    async def test_inactive_prompts_excluded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Deactivated prompts must not appear in the list — they're kept
        in the DB so existing UserPrompt answers still reference valid rows,
        but they should no longer be offered to new users."""
        await seed_prompts(db_session, [
            {"question": "active prompt", "category": "fun", "language": "fa", "is_active": True},
            {"question": "hidden prompt", "category": "fun", "language": "fa", "is_active": False},
        ])
        res = await client.get(PROMPTS_URL)
        questions = [item["question"] for item in res.json()]
        assert "active prompt" in questions
        assert "hidden prompt" not in questions

    async def test_only_inactive_prompts_returns_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_prompts(db_session, [
            {"question": "hidden prompt 1", "category": "fun", "language": "fa", "is_active": False},
            {"question": "hidden prompt 2", "category": "deep", "language": "fa", "is_active": False},
        ])
        res = await client.get(PROMPTS_URL)
        assert res.json() == []

    async def test_is_active_not_exposed_in_response(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The is_active flag is internal — the response schema doesn't
        include it (clients shouldn't know a prompt was 'hidden')."""
        await seed_prompts(db_session, FA_PROMPTS[:1])
        item = (await client.get(PROMPTS_URL)).json()[0]
        assert "is_active" not in item

    async def test_mixed_active_inactive_across_languages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Active/inactive filtering works independently per language."""
        await seed_prompts(db_session, [
            {"question": "active fa", "category": "fun", "language": "fa", "is_active": True},
            {"question": "inactive fa", "category": "fun", "language": "fa", "is_active": False},
            {"question": "active en", "category": "fun", "language": "en", "is_active": True},
            {"question": "inactive en", "category": "fun", "language": "en", "is_active": False},
        ])
        fa_questions = [i["question"] for i in (await client.get(PROMPTS_URL, params={"language": "fa"})).json()]
        en_questions = [i["question"] for i in (await client.get(PROMPTS_URL, params={"language": "en"})).json()]
        assert fa_questions == ["active fa"]
        assert en_questions == ["active en"]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

class TestPromptsCategories:

    async def test_all_seeded_categories_present(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        all_categories = ["about_me", "lifestyle", "relationships", "fun", "deep", "interests"]
        rows = [
            {"question": f"prompt {cat}", "category": cat, "language": "fa"}
            for cat in all_categories
        ]
        await seed_prompts(db_session, rows)
        res = await client.get(PROMPTS_URL)
        returned_cats = {item["category"] for item in res.json()}
        assert returned_cats == set(all_categories)

    async def test_category_can_be_null(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, [
            {"question": "uncategorized prompt", "category": None, "language": "fa"}
        ])
        res = await client.get(PROMPTS_URL)
        assert res.status_code == 200
        assert res.json()[0]["category"] is None

    async def test_can_filter_fa_by_category_client_side(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The API doesn't filter by category server-side (Flutter does it),
        so all categories come back in one call — verify the client can
        group them without making multiple requests."""
        await seed_prompts(db_session, FA_PROMPTS)
        res = await client.get(PROMPTS_URL)
        items = res.json()
        by_category = {}
        for item in items:
            by_category.setdefault(item["category"], []).append(item)
        assert "about_me" in by_category
        assert "lifestyle" in by_category


# ---------------------------------------------------------------------------
# Composite unique constraint behavior
# ---------------------------------------------------------------------------

class TestPromptsCompositeUnique:

    async def test_same_question_exists_in_both_languages(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The composite unique constraint is (question, language) — the
        same question text can't exist twice in the same language, but it
        CAN exist in both 'fa' and 'en'. This is the core design decision
        that enables bilingual prompts without a separate translation table."""
        question = "My favorite prompt question"
        await seed_prompts(db_session, [
            {"question": question, "category": "fun", "language": "fa"},
            {"question": question, "category": "fun", "language": "en"},
        ])
        fa_items = (await client.get(PROMPTS_URL, params={"language": "fa"})).json()
        en_items = (await client.get(PROMPTS_URL, params={"language": "en"})).json()
        assert any(item["question"] == question for item in fa_items)
        assert any(item["question"] == question for item in en_items)

    async def test_duplicate_question_same_language_not_inserted_twice(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The DB constraint (uq_prompts_question_language) ensures the same
        question can't exist twice in the same language. Inserting a
        duplicate should raise a DB error, not silently create two rows."""
        from sqlalchemy.exc import IntegrityError
        question = "This prompt will be duplicated"
        await seed_prompts(db_session, [
            {"question": question, "category": "fun", "language": "fa"},
        ])
        with pytest.raises(IntegrityError):
            await seed_prompts(db_session, [
                {"question": question, "category": "deep", "language": "fa"},
            ])


# ---------------------------------------------------------------------------
# Access control — public endpoint
# ---------------------------------------------------------------------------

class TestPromptsAccessControl:

    async def test_no_auth_header_required(self, client: AsyncClient, db_session: AsyncSession):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        res = await client.get(PROMPTS_URL)
        assert res.status_code == 200

    async def test_garbage_auth_header_does_not_cause_401(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        await seed_prompts(db_session, FA_PROMPTS[:1])
        res = await client.get(
            PROMPTS_URL,
            headers={"Authorization": "Bearer garbage-token"},
        )
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Scale — mimics the real 116-row bilingual seed
# ---------------------------------------------------------------------------

class TestPromptsScale:

    async def test_handles_full_58_fa_prompts(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rows = [
            {"question": f"سوال {i}", "category": "about_me", "language": "fa"}
            for i in range(58)
        ]
        await seed_prompts(db_session, rows)
        res = await client.get(PROMPTS_URL, params={"language": "fa"})
        assert res.status_code == 200
        assert len(res.json()) == 58

    async def test_handles_full_bilingual_116_rows(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        fa_rows = [
            {"question": f"سوال {i}", "category": "about_me", "language": "fa"}
            for i in range(58)
        ]
        en_rows = [
            {"question": f"Question {i}", "category": "about_me", "language": "en"}
            for i in range(58)
        ]
        await seed_prompts(db_session, fa_rows + en_rows)
        fa_count = len((await client.get(PROMPTS_URL, params={"language": "fa"})).json())
        en_count = len((await client.get(PROMPTS_URL, params={"language": "en"})).json())
        assert fa_count == 58
        assert en_count == 58