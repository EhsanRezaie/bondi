"""prompt table

Revision ID: 30e15dca97b1
Revises: 1b1463a8643a
Create Date: 2026-06-22 13:42:15.484934

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30e15dca97b1'
down_revision: Union[str, Sequence[str], None] = '1b1463a8643a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
