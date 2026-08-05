"""update the prompt table

Revision ID: 1b1463a8643a
Revises: d05897b084f5
Create Date: 2026-06-22 13:38:35.420375

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b1463a8643a'
down_revision: Union[str, Sequence[str], None] = '16268284c9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
