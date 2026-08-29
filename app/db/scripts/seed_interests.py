"""
Seed / sync the `interests` table from app/db/seed_data/interests.json.

Behavior:
    - Existing rows (matched by unique `name`) are UPDATED if `category` or
      `icon` changed in the JSON file.
    - Rows present in the JSON but not yet in the DB are INSERTED.
    - Rows already in the DB that are no longer in the JSON are left untouched
      (never deleted) — safe to run after manually adding rows elsewhere.
    - Safe to re-run any number of times (idempotent upsert).

Usage:
    python -m app.db.seed_interests
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import AsyncSessionLocal
from app.models.interest import Interest

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent.parent / "seed_data" / "interests.json"


def load_seed_data() -> list[dict]:
    """Read and validate interests.json before touching the database."""
    if not SEED_FILE.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_FILE}")

    with open(SEED_FILE, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("interests.json must contain a JSON array")

    names = [item.get("name") for item in data]
    if len(names) != len(set(names)):
        dupes = {n for n in names if names.count(n) > 1}
        raise ValueError(f"Duplicate 'name' keys in interests.json: {dupes}")

    for item in data:
        if not item.get("name"):
            raise ValueError(f"Interest missing required 'name' field: {item}")

    return data


async def seed_interests() -> None:
    data = load_seed_data()
    logger.info("Loaded %d interests from %s", len(data), SEED_FILE)

    async with AsyncSessionLocal() as session:
        # Snapshot existing rows first so we can report inserted vs updated counts.
        from sqlalchemy import select

        existing_names = set(
            (await session.execute(select(Interest.name))).scalars().all()
        )

        stmt = pg_insert(Interest).values(data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Interest.name],
            set_={
                "category": stmt.excluded.category,
                "icon": stmt.excluded.icon,
                "translations": stmt.excluded.translations,
            },
        )

        await session.execute(stmt)
        await session.commit()

        seed_names = {item["name"] for item in data}
        inserted = seed_names - existing_names
        updated = seed_names & existing_names

        logger.info(
            "Seed complete: %d inserted, %d updated (existing untouched: %d)",
            len(inserted),
            len(updated),
            len(existing_names - seed_names),
        )
        print(f"✅ Interests seeded: {len(inserted)} inserted, {len(updated)} updated.")
        if inserted:
            print(f"   New: {', '.join(sorted(inserted))}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(seed_interests())


if __name__ == "__main__":
    main()