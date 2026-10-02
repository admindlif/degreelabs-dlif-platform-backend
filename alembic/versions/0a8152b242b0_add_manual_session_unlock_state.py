"""add manual session unlock state

Revision ID: 0a8152b242b0
Revises: 585947a40df9
Create Date: 2026-09-26 18:52:38.467352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a8152b242b0'
down_revision: Union[str, Sequence[str], None] = '585947a40df9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Admin-controlled Session unlock state."""

    op.add_column(
        "sessions",
        sa.Column(
            "is_unlocked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # Initial rollout:
    # Sessions 0, 1 and 2 are available immediately.
    # Sessions 3+ stay locked until Admin explicitly unlocks them.
    #
    # unlock_at now means:
    # "the time Admin actually unlocked this Session".
    op.execute(
        """
        UPDATE sessions
        SET
            is_unlocked =
                CASE
                    WHEN session_number <= 2 THEN true
                    ELSE false
                END,
            unlock_at =
                CASE
                    WHEN session_number <= 2
                        THEN COALESCE(unlock_at, NOW())
                    ELSE NULL
                END
        """
    )


def downgrade() -> None:
    """Restore previous date-based unlock metadata."""

    # Restore the behaviour from migration 585947a40df9
    # before removing is_unlocked.
    op.execute(
        """
        UPDATE sessions
        SET unlock_at =
            CASE
                WHEN session_number <= 2
                    THEN COALESCE(unlock_at, NOW())
                ELSE start_at
            END
        """
    )

    op.drop_column(
        "sessions",
        "is_unlocked",
    )