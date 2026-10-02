"""add fellow role and handbook alignment

Revision ID: d51e7399a0ff
Revises: c43f0e383efd
Create Date: 2026-09-25 11:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd51e7399a0ff'
down_revision: Union[str, Sequence[str], None] = 'c43f0e383efd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'fellow' to user_role enum and update existing student rows."""
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        # PostgreSQL requires ALTER TYPE ADD VALUE outside an active transaction
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'fellow'")
        op.execute("UPDATE users SET role = 'fellow' WHERE role = 'student'")


def downgrade() -> None:
    """Revert fellow to student."""
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute("UPDATE users SET role = 'student' WHERE role = 'fellow'")
