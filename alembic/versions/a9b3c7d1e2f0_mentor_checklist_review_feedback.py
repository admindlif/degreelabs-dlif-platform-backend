"""mentor_checklist_review_feedback

Revision ID: a9b3c7d1e2f0
Revises: b2f4c6e8a0d1
Create Date: 2026-10-26 14:50:00.000000

Adds:
- checklist_review_status enum
- fellow_checklist_completions: contribution, reflection, review_status,
  reviewed_by_mentor_id, reviewed_at, rejection_reason columns
- checklist_feedback_messages table (threaded per fellow+item)
- mentor_team_assignments table
- checklist_items: required_submission_session_id column

Existing is_completed / completed_at rows are preserved unchanged.
Existing completions default to 'not_checked' unless is_completed=true,
in which case they migrate to 'pending_review' (awaiting mentor review).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a9b3c7d1e2f0"
down_revision: Union[str, Sequence[str], None] = "b2f4c6e8a0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New ENUM type
    # ------------------------------------------------------------------
    checklist_review_status = postgresql.ENUM(
        "not_checked",
        "pending_review",
        "accepted",
        "rejected",
        name="checklist_review_status",
        create_type=False,
    )
    checklist_review_status.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. Extend fellow_checklist_completions
    # ------------------------------------------------------------------
    op.add_column(
        "fellow_checklist_completions",
        sa.Column("contribution", sa.Text(), nullable=True),
    )
    op.add_column(
        "fellow_checklist_completions",
        sa.Column("reflection", sa.Text(), nullable=True),
    )
    op.add_column(
        "fellow_checklist_completions",
        sa.Column(
            "review_status",
            sa.Enum(
                "not_checked",
                "pending_review",
                "accepted",
                "rejected",
                name="checklist_review_status",
            ),
            nullable=False,
            server_default="not_checked",
        ),
    )
    op.add_column(
        "fellow_checklist_completions",
        sa.Column(
            "reviewed_by_mentor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "fellow_checklist_completions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fellow_checklist_completions",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )

    # FK for reviewed_by_mentor_id
    op.create_foreign_key(
        "fk_completion_reviewed_by_mentor",
        "fellow_checklist_completions",
        "users",
        ["reviewed_by_mentor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Migrate existing completed rows to pending_review so mentor can review them
    op.execute(
        """
        UPDATE fellow_checklist_completions
        SET review_status = 'pending_review'
        WHERE is_completed = true
        """
    )

    # Add index on review_status for mentor dashboard queries
    op.create_index(
        "ix_fellow_checklist_completions_review_status",
        "fellow_checklist_completions",
        ["review_status"],
    )

    # ------------------------------------------------------------------
    # 3. Add required_submission_session_id to checklist_items
    # ------------------------------------------------------------------
    op.add_column(
        "checklist_items",
        sa.Column(
            "required_submission_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_checklist_item_required_session",
        "checklist_items",
        "sessions",
        ["required_submission_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_checklist_items_required_submission_session",
        "checklist_items",
        ["required_submission_session_id"],
    )

    # ------------------------------------------------------------------
    # 4. Create checklist_feedback_messages
    # ------------------------------------------------------------------
    op.create_table(
        "checklist_feedback_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checklist_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fellow_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_role", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"],
            ["checklist_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fellow_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_checklist_feedback_messages_checklist_item_id",
        "checklist_feedback_messages",
        ["checklist_item_id"],
    )
    op.create_index(
        "ix_checklist_feedback_messages_fellow_user_id",
        "checklist_feedback_messages",
        ["fellow_user_id"],
    )
    op.create_index(
        "ix_checklist_feedback_messages_author_user_id",
        "checklist_feedback_messages",
        ["author_user_id"],
    )
    op.create_index(
        "ix_checklist_feedback_messages_created_at",
        "checklist_feedback_messages",
        ["created_at"],
    )

    # ------------------------------------------------------------------
    # 5. Create mentor_team_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "mentor_team_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mentor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mentor_user_id", "team_id", name="uq_mentor_team"),
        sa.ForeignKeyConstraint(
            ["mentor_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["cohorts.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mentor_team_assignments_mentor_user_id",
        "mentor_team_assignments",
        ["mentor_user_id"],
    )
    op.create_index(
        "ix_mentor_team_assignments_team_id",
        "mentor_team_assignments",
        ["team_id"],
    )
    op.create_index(
        "ix_mentor_team_assignments_cohort_id",
        "mentor_team_assignments",
        ["cohort_id"],
    )


def downgrade() -> None:
    # 5. Drop mentor_team_assignments
    op.drop_index("ix_mentor_team_assignments_cohort_id", table_name="mentor_team_assignments")
    op.drop_index("ix_mentor_team_assignments_team_id", table_name="mentor_team_assignments")
    op.drop_index("ix_mentor_team_assignments_mentor_user_id", table_name="mentor_team_assignments")
    op.drop_table("mentor_team_assignments")

    # 4. Drop checklist_feedback_messages
    op.drop_index("ix_checklist_feedback_messages_created_at", table_name="checklist_feedback_messages")
    op.drop_index("ix_checklist_feedback_messages_author_user_id", table_name="checklist_feedback_messages")
    op.drop_index("ix_checklist_feedback_messages_fellow_user_id", table_name="checklist_feedback_messages")
    op.drop_index("ix_checklist_feedback_messages_checklist_item_id", table_name="checklist_feedback_messages")
    op.drop_table("checklist_feedback_messages")

    # 3. Drop required_submission_session_id from checklist_items
    op.drop_constraint("fk_checklist_item_required_session", "checklist_items", type_="foreignkey")
    op.drop_index("ix_checklist_items_required_submission_session", table_name="checklist_items")
    op.drop_column("checklist_items", "required_submission_session_id")

    # 2. Drop extended columns from fellow_checklist_completions
    op.drop_index("ix_fellow_checklist_completions_review_status", table_name="fellow_checklist_completions")
    op.drop_constraint("fk_completion_reviewed_by_mentor", "fellow_checklist_completions", type_="foreignkey")
    op.drop_column("fellow_checklist_completions", "rejection_reason")
    op.drop_column("fellow_checklist_completions", "reviewed_at")
    op.drop_column("fellow_checklist_completions", "reviewed_by_mentor_id")
    op.drop_column("fellow_checklist_completions", "review_status")
    op.drop_column("fellow_checklist_completions", "reflection")
    op.drop_column("fellow_checklist_completions", "contribution")

    # 1. Drop ENUM
    sa.Enum(name="checklist_review_status").drop(op.get_bind(), checkfirst=True)
