# alembic/versions/f0a1b2c3d4e5_add_is_searchable_flag.py
"""Denormalized is_searchable flag on user_profiles.

Replaces the per-request photo semi/anti-join pair in search and the
discover deck refill with a boolean column:
    is_searchable = EXISTS(approved photo) AND NOT EXISTS(non-approved photo)
Maintained by PhotoService.recompute_is_searchable at every photo status
event (upload, delete, admin approve, admin reject) — same hooks that bump
the photo epoch. Backfilled here for existing data.
"""

import sqlalchemy as sa

from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("is_searchable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE user_profiles SET is_searchable = "
        "EXISTS (SELECT 1 FROM photos p WHERE p.user_id = user_id AND p.status = 'approved') "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM photos p2 WHERE p2.user_id = user_id AND p2.status <> 'approved')"
    )
    op.create_index("idx_profiles_searchable", "user_profiles", ["is_searchable"])


def downgrade() -> None:
    op.drop_index("idx_profiles_searchable", table_name="user_profiles")
    op.drop_column("user_profiles", "is_searchable")
