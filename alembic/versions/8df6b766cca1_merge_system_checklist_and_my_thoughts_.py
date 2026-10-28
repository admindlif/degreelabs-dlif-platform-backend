"""merge_system_checklist_and_my_thoughts_heads

Revision ID: 8df6b766cca1
Revises: a1b2c3d4e5f6, b8c9d0e1f2a3
Create Date: 2026-10-28 16:56:24.476247

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8df6b766cca1'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'b8c9d0e1f2a3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
