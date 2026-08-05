"""add device_tokens table

Revision ID: a1b2c3d4e5f6
Revises: d9d7e584c621
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a1b2c3d4e5f6"
down_revision = "7f10ad4c02b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "token", name="uq_device_token_user_token"),
    )
    op.create_index("idx_device_tokens_user", "device_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_device_tokens_user", table_name="device_tokens")
    op.drop_table("device_tokens")
