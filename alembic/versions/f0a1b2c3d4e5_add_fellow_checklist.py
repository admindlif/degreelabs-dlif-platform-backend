"""add fellow checklist

Revision ID: f0a1b2c3d4e5
Revises: bf3f9d75fe33
Create Date: 2026-09-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "bf3f9d75fe33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=True),
        sa.Column("phase_id", sa.UUID(), nullable=True),
        sa.Column("week_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_label", sa.String(length=100), nullable=True),
        sa.Column("action_url", sa.String(length=1000), nullable=True),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cohort_id"], ["cohorts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["phase_id"], ["phases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checklist_items_cohort_id", "checklist_items", ["cohort_id"])
    op.create_index("ix_checklist_items_phase_id", "checklist_items", ["phase_id"])
    op.create_index("ix_checklist_items_week_id", "checklist_items", ["week_id"])
    op.create_index("ix_checklist_items_session_id", "checklist_items", ["session_id"])
    op.create_index("ix_checklist_items_due_at", "checklist_items", ["due_at"])
    op.create_index("ix_checklist_items_is_active", "checklist_items", ["is_active"])

    op.create_table(
        "fellow_checklist_completions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("checklist_item_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["checklist_item_id"], ["checklist_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checklist_item_id", "user_id", name="uq_checklist_item_user_completion"),
    )
    op.create_index(
        "ix_fellow_checklist_completions_checklist_item_id",
        "fellow_checklist_completions",
        ["checklist_item_id"],
    )
    op.create_index(
        "ix_fellow_checklist_completions_user_id",
        "fellow_checklist_completions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fellow_checklist_completions_user_id",
        table_name="fellow_checklist_completions",
    )
    op.drop_index(
        "ix_fellow_checklist_completions_checklist_item_id",
        table_name="fellow_checklist_completions",
    )
    op.drop_table("fellow_checklist_completions")
    op.drop_index("ix_checklist_items_is_active", table_name="checklist_items")
    op.drop_index("ix_checklist_items_due_at", table_name="checklist_items")
    op.drop_index("ix_checklist_items_session_id", table_name="checklist_items")
    op.drop_index("ix_checklist_items_week_id", table_name="checklist_items")
    op.drop_index("ix_checklist_items_phase_id", table_name="checklist_items")
    op.drop_index("ix_checklist_items_cohort_id", table_name="checklist_items")
    op.drop_table("checklist_items")
