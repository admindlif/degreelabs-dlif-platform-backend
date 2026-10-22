"""add_recording_processing_state

Revision ID: 61fa213d09bd
Revises: e7b2c9a1d4f8
Create Date: 2026-10-13 10:43:27.821684

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "61fa213d09bd"
down_revision: Union[str, Sequence[str], None] = "e7b2c9a1d4f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum definition reused in upgrade and downgrade
_rps_enum = sa.Enum(
    "pending", "processing", "ready", "failed",
    name="recording_processing_status",
)


def upgrade() -> None:
    """Add R2 recording-processing state columns to sessions.

    recording_processing_status is NOT NULL with server_default='pending'.
    Upgrade steps:
      1. Create the PG enum type (checkfirst=True is idempotent).
      2. Add column as nullable with server_default so existing rows get filled.
      3. Back-fill any remaining NULLs (safety net).
      4. ALTER COLUMN … SET NOT NULL.
      5. Add the three remaining nullable columns.
    """
    # 1. Create the enum type idempotently
    _rps_enum.create(op.get_bind(), checkfirst=True)

    # 2. Add column as nullable first
    op.add_column(
        "sessions",
        sa.Column(
            "recording_processing_status",
            sa.Enum(
                "pending", "processing", "ready", "failed",
                name="recording_processing_status",
                create_type=False,
            ),
            server_default=sa.text("'pending'"),
            nullable=True,
        ),
    )

    # 3. Back-fill pre-existing NULLs (belt-and-braces)
    op.get_bind().execute(sa.text(
        "UPDATE sessions "
        "SET recording_processing_status = 'pending' "
        "WHERE recording_processing_status IS NULL"
    ))

    # 4. Enforce NOT NULL
    op.alter_column(
        "sessions",
        "recording_processing_status",
        nullable=False,
    )

    # 5. Remaining nullable columns
    op.add_column(
        "sessions",
        sa.Column("recording_r2_key", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("recording_processing_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove R2 recording-processing state columns from sessions."""
    op.drop_column("sessions", "recording_processing_error")
    op.drop_column("sessions", "recording_processed_at")
    op.drop_column("sessions", "recording_r2_key")
    op.drop_column("sessions", "recording_processing_status")
    _rps_enum.drop(op.get_bind(), checkfirst=True)
