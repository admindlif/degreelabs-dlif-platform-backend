"""Add optional My Thoughts to Fellow checklist responses.

Revision ID: b8c9d0e1f2a3
Revises: d1e2f3a4b5c6
"""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fellow_checklist_completions",
        sa.Column("my_thoughts", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fellow_checklist_completions", "my_thoughts")
