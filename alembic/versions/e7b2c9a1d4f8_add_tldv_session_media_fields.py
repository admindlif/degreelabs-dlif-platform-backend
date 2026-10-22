"""add tldv session media fields

Revision ID: e7b2c9a1d4f8
Revises: f0a1b2c3d4e5
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e7b2c9a1d4f8"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add nullable tldv_meeting_id and index
    op.add_column(
        "sessions",
        sa.Column("tldv_meeting_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_sessions_tldv_meeting_id",
        "sessions",
        ["tldv_meeting_id"],
        unique=False,
    )

    # 2. Add recording_status
    op.add_column(
        "sessions",
        sa.Column("recording_status", sa.String(length=50), nullable=True),
    )

    # 3. Add recording_duration_seconds
    op.add_column(
        "sessions",
        sa.Column("recording_duration_seconds", sa.Integer(), nullable=True),
    )

    # 4. Add structured transcript_data (JSONB for PostgreSQL, generic JSON fallback)
    op.add_column(
        "sessions",
        sa.Column(
            "transcript_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "transcript_data")
    op.drop_column("sessions", "recording_duration_seconds")
    op.drop_column("sessions", "recording_status")
    op.drop_index("ix_sessions_tldv_meeting_id", table_name="sessions")
    op.drop_column("sessions", "tldv_meeting_id")
