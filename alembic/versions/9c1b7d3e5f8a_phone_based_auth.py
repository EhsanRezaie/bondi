"""phone-based auth: phone required+unique, email optional, drop google/password

Revision ID: 9c1b7d3e5f8a
Revises: f8a2c9e05b1d
Create Date: 2026-08-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9c1b7d3e5f8a'
down_revision: Union[str, Sequence[str], None] = 'f8a2c9e05b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) Widen phone column and backfill any existing NULL phones so the
    #    NOT NULL constraint can be applied. Placeholder numbers are unique
    #    (derived from the row's UUID) and can be replaced later.
    op.alter_column('users', 'phone', existing_type=sa.String(length=20), type_=sa.String(length=32), nullable=True)
    op.execute("""
        UPDATE users
        SET phone = '+00000000000' || replace(id::text, '-', '')
        WHERE phone IS NULL OR btrim(phone) = ''
    """)
    op.alter_column('users', 'phone', existing_type=sa.String(length=32), nullable=False)
    op.create_index('ix_users_phone', 'users', ['phone'], unique=True)

    # 2) Email is now optional (still unique; Postgres allows multiple NULLs).
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=True)

    # 3) Drop columns no longer used (Google login and password auth removed).
    op.drop_column('users', 'google_id')
    op.drop_column('users', 'password_hash')

    # 4) Registration status now tracks phone-based verification.
    op.alter_column(
        'users',
        'registration_status',
        existing_type=sa.String(length=20),
        server_default='phone_pending',
        nullable=False,
    )
    # Normalize any legacy status values.
    op.execute("UPDATE users SET registration_status = 'phone_verified' WHERE registration_status = 'email_verified'")
    op.execute("UPDATE users SET registration_status = 'phone_pending' WHERE registration_status = 'email_pending'")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'users',
        'registration_status',
        existing_type=sa.String(length=20),
        server_default='email_pending',
        nullable=False,
    )
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('google_id', sa.String(length=255), nullable=True))
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=False)
    op.drop_index('ix_users_phone', table_name='users')
    op.alter_column('users', 'phone', existing_type=sa.String(length=32), type_=sa.String(length=20), nullable=True)
