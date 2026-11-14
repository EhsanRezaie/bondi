"""
Seed / sync the `prompts` table from app/db/seed_data/prompts.json.

Behavior:
    - Existing rows (matched by unique `question` + `language` pair) are
      UPDATED if `category` changed in the JSON file.
    - Rows present in the JSON but not yet in the DB are INSERTED.
    - Rows already in the DB that are no longer in the JSON are left
      untouched (never deleted).
    - Safe to re-run any number of times (idempotent upsert).
    - Each prompt question can exist multiple times in the table as long
      as each copy has a different `language` (e.g. the same question in
      both "fa" and "en") — the unique constraint is on the (question,
      language) pair, not on question alone.

Requires: Prompt must have a UNIQUE constraint on (question, language)
(see uq_prompts_question_language in app/models/prompt.py).

Usage:
    python -m app.db.scripts.seed_prompts
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import AsyncSessionLocal
from app.models.prompt import Prompt

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent.parent / "seed_data" / "prompts.json"


def load_seed_data() -> list[dict]:
    """Read and validate prompts.json before touching the database."""
    if not SEED_FILE.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_FILE}")

    with open(SEED_FILE, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("prompts.json must contain a JSON array")

    pairs = [(item.get("question"), item.get("language")) for item in data]
    if len(pairs) != len(set(pairs)):
        dupes = {p for p in pairs if pairs.count(p) > 1}
        raise ValueError(f"Duplicate (question, language) pairs in prompts.json: {dupes}")

    for item in data:
        if not item.get("question"):
            raise ValueError(f"Prompt missing required 'question' field: {item}")
        if not item.get("language"):
            raise ValueError(f"Prompt missing required 'language' field: {item}")

    return data


async def seed_prompts() -> None:
    data = load_seed_data()
    logger.info("Loaded %d prompts from %s", len(data), SEED_FILE)

    async with AsyncSessionLocal() as session:
        existing_pairs = set(
            (await session.execute(select(Prompt.question, Prompt.language))).all()
        )

        stmt = pg_insert(Prompt).values(data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Prompt.question, Prompt.language],
            set_={
                "category": stmt.excluded.category,
            },
        )

        await session.execute(stmt)
        await session.commit()

        seed_pairs = {(item["question"], item["language"]) for item in data}
        inserted = seed_pairs - existing_pairs
        updated = seed_pairs & existing_pairs

        logger.info(
            "Seed complete: %d inserted, %d updated (existing untouched: %d)",
            len(inserted),
            len(updated),
            len(existing_pairs - seed_pairs),
        )
        print(f"✅ Prompts seeded: {len(inserted)} inserted, {len(updated)} updated.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(seed_prompts())


if __name__ == "__main__":
    main()
