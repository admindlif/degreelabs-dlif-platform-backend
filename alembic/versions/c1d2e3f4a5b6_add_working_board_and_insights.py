"""add_working_board_and_insights

Revision ID: c1d2e3f4a5b6
Revises: a9b3c7d1e2f0
Create Date: 2026-10-26 21:00:00.000000

Adds:
- working_boards table
- working_board_insights table
- working_board_insight_revisions table
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a9b3c7d1e2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. working_boards
    # ------------------------------------------------------------------
    op.create_table(
        "working_boards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False, server_default="Working Board"),
        sa.Column("working_board_url", sa.String(length=1500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.UniqueConstraint("session_id", "team_id", name="uq_session_team_working_board"),
    )
    op.create_index("ix_working_boards_session_id", "working_boards", ["session_id"])
    op.create_index("ix_working_boards_team_id", "working_boards", ["team_id"])

    # ------------------------------------------------------------------
    # 2. working_board_insights
    # ------------------------------------------------------------------
    op.create_table(
        "working_board_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "working_board_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mentor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("insights_text", sa.Text(), nullable=False),
        sa.Column(
            "publication_status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("working_board_id", name="uq_working_board_insights_board"),
    )
    op.create_index(
        "ix_working_board_insights_working_board_id",
        "working_board_insights",
        ["working_board_id"],
    )
    op.create_index(
        "ix_working_board_insights_mentor_user_id",
        "working_board_insights",
        ["mentor_user_id"],
    )

    # ------------------------------------------------------------------
    # 3. working_board_insight_revisions
    # ------------------------------------------------------------------
    op.create_table(
        "working_board_insight_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "insight_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_board_insights.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "working_board_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("working_boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mentor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("insights_text", sa.Text(), nullable=False),
        sa.Column("publication_status", sa.String(length=20), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_working_board_insight_revisions_insight_id",
        "working_board_insight_revisions",
        ["insight_id"],
    )
    op.create_index(
        "ix_working_board_insight_revisions_working_board_id",
        "working_board_insight_revisions",
        ["working_board_id"],
    )


def downgrade() -> None:
    op.drop_table("working_board_insight_revisions")
    op.drop_table("working_board_insights")
    op.drop_table("working_boards")
