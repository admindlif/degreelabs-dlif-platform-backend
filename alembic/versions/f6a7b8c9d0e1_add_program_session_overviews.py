"""add program_session_overviews table

Revision ID: f6a7b8c9d0e1
Revises: e4f5a6b7c8d9
Create Date: 2026-10-27 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "program_session_overviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("phase_code", sa.String(length=50), nullable=False),
        sa.Column("session_number", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("what_to_explore", sa.Text(), nullable=False),
        sa.Column("working_evidence", sa.Text(), nullable=False),
        sa.Column(
            "source_document",
            sa.String(length=255),
            nullable=False,
            server_default="DLIF Fellow Handbook – DISCOVER",
        ),
        sa.Column(
            "source_version",
            sa.String(length=50),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "phase_code",
            "session_number",
            "source_version",
            name="uq_program_session_overview_phase_session_version",
        ),
    )
    op.create_index(
        "ix_program_session_overviews_phase_code",
        "program_session_overviews",
        ["phase_code"],
        unique=False,
    )
    op.create_index(
        "ix_program_session_overviews_session_number",
        "program_session_overviews",
        ["session_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_program_session_overviews_session_number",
        table_name="program_session_overviews",
    )
    op.drop_index(
        "ix_program_session_overviews_phase_code",
        table_name="program_session_overviews",
    )
    op.drop_table("program_session_overviews")
