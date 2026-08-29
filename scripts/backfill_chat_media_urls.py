"""
Backfill chat messages whose `media_url` still holds a full presigned URL
(pre-key storage) so they store just the object key instead.

New sends store keys (see MediaService.save_photo/save_voice); old rows hold
expired presigned URLs. `MediaService.resolve_media_url()` already handles
legacy URLs at read time, but this normalizes the DB so keys are the single
source of truth.

Usage:
    python -m scripts.backfill_chat_media_urls [--dry-run]
"""

import asyncio
import sys
from urllib.parse import urlparse

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.message import Message


def extract_key(media_url: str) -> str:
    """Pull the object key out of a presigned URL path: /{bucket}/{key}."""
    if not (media_url.startswith("http://") or media_url.startswith("https://")):
        return media_url
    path = urlparse(media_url).path.lstrip("/")
    parts = path.split("/", 1)
    return parts[1] if len(parts) > 1 and parts[1] else parts[0]


async def backfill(dry_run: bool = True) -> dict:
    updated = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Message).where(
                Message.media_url.is_not(None),
                Message.media_url.like("http%"),
            )
        )
        rows = result.scalars().all()
        for msg in rows:
            key = extract_key(msg.media_url)
            if not key.startswith("chat/"):
                skipped += 1
                continue
            if not dry_run:
                msg.media_url = key
            updated += 1
        if not dry_run:
            await session.commit()
    return {"rows": len(rows), "updated": updated, "skipped": skipped}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    print(f"Running backfill ({'dry-run' if dry_run else 'apply'})...")
    result = asyncio.run(backfill(dry_run=dry_run))
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
