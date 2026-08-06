"""
Rotate ENCRYPTION_SECRET: decrypt all messages with the old secret,
re-encrypt with the new secret, and update the DB in batches.

This script is designed for maintenance windows. The app must be
stopped (or in read-only mode) while it runs so no new messages
are written with the old key mid-rotation.

Usage:
    # Dry run — report what would change without writing:
    python -m app.db.scripts.rotate_encryption_secret \
        --old-secret <CURRENT_SECRET> \
        --new-secret <NEW_SECRET> \
        --dry-run

    # Apply the rotation:
    python -m app.db.scripts.rotate_encryption_secret \
        --old-secret <CURRENT_SECRET> \
        --new-secret <NEW_SECRET> \
        --apply

    # Custom batch size (default 500):
    python -m app.db.scripts.rotate_encryption_secret \
        --old-secret <CURRENT_SECRET> \
        --new-secret <NEW_SECRET> \
        --apply \
        --batch-size 200
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.message import Message
from app.core.encryption import decrypt_with_secret, encrypt_with_secret

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
DRY_RUN_BANNER = "DRY RUN — no writes will be performed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rotate ENCRYPTION_SECRET by re-encrypting all messages."
    )
    parser.add_argument(
        "--old-secret",
        required=True,
        help="The current ENCRYPTION_SECRET value in the running .env",
    )
    parser.add_argument(
        "--new-secret",
        required=True,
        help="The new ENCRYPTION_SECRET to rotate to",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report counts without writing any changes",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually perform the re-encryption (requires --dry-run to be absent)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of rows to process per batch (default {BATCH_SIZE})",
    )
    return parser.parse_args()


async def _process_batch(
    session: AsyncSession,
    messages: list[Message],
    old_secret: str,
    new_secret: str,
    dry_run: bool,
) -> tuple[int, int, int]:
    """
    Decrypt and re-encrypt a batch of messages.

    Returns:
        (re_encrypted, skipped, errors)
    """
    re_encrypted = 0
    skipped = 0
    errors = 0

    for msg in messages:
        if not msg._content:
            skipped += 1
            continue

        try:
            plaintext = decrypt_with_secret(msg._content, str(msg.chat_id), old_secret)
        except Exception as exc:
            # Content is not valid ciphertext (e.g. plaintext marker like
            # "[Message deleted]" or a voice message with empty content).
            # Leave it untouched.
            logger.debug(
                "Skipping message %s (not valid ciphertext or plaintext marker): %s",
                msg.id,
                exc,
            )
            skipped += 1
            continue

        if dry_run:
            re_encrypted += 1
            continue

        try:
            new_cipher = encrypt_with_secret(plaintext, str(msg.chat_id), new_secret)
        except Exception as exc:
            logger.error("Re-encrypt failed for message %s: %s", msg.id, exc)
            errors += 1
            continue

        msg._content = new_cipher
        re_encrypted += 1

    if not dry_run and re_encrypted > 0:
        await session.commit()

    return re_encrypted, skipped, errors


async def run_rotation(
    old_secret: str,
    new_secret: str,
    dry_run: bool,
    batch_size: int,
) -> None:
    label = DRY_RUN_BANNER if dry_run else "APPLY"
    logger.info("Starting ENCRYPTION_SECRET rotation [%s]", label)
    logger.info("Old secret: %s*** (len=%d)", old_secret[:6], len(old_secret))
    logger.info("New secret: %s*** (len=%d)", new_secret[:6], len(new_secret))

    if dry_run and not dry_run:
        pass  # unreachable, kept for clarity

    total_re_encrypted = 0
    total_skipped = 0
    total_errors = 0
    total_processed = 0

    async with AsyncSessionLocal() as session:
        last_id = None
        batch_num = 0

        while True:
            stmt = (
                select(Message)
                .where(Message.chat_id.is_not(None))
                .order_by(Message.id)
            )
            if last_id is not None:
                stmt = stmt.where(Message.id > last_id)
            stmt = stmt.limit(batch_size)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            if not rows:
                break

            batch_num += 1
            logger.info(
                "Batch %d: processing %d messages (last_id=%s)",
                batch_num,
                len(rows),
                last_id,
            )

            re_encrypted, skipped, errors = await _process_batch(
                session, rows, old_secret, new_secret, dry_run
            )

            total_re_encrypted += re_encrypted
            total_skipped += skipped
            total_errors += errors
            total_processed += len(rows)

            last_id = rows[-1].id

            # If we got fewer rows than batch_size, we're at the end.
            if len(rows) < batch_size:
                break

    logger.info(
        "Rotation complete — processed=%d re_encrypted=%d skipped=%d errors=%d",
        total_processed,
        total_re_encrypted,
        total_skipped,
        total_errors,
    )

    if dry_run:
        print(f"[DRY RUN] Would re-encrypt {total_re_encrypted} messages "
              f"({total_skipped} skipped, {total_errors} errors)")
    else:
        print(f"Re-encrypted {total_re_encrypted} messages "
              f"({total_skipped} skipped, {total_errors} errors)")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    args = parse_args()

    if args.dry_run and args.apply:
        print("Error: use either --dry-run or --apply, not both.", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run and not args.apply:
        print(
            "Error: you must specify either --dry-run or --apply.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.apply:
        print(
            "WARNING: This will RE-ENCRYPT all messages in the database. "
            "Ensure you have a pg_dump backup before proceeding.",
            file=sys.stderr,
        )
        confirm = input("Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    asyncio.run(run_rotation(args.old_secret, args.new_secret, args.dry_run, args.batch_size))


if __name__ == "__main__":
    main()
