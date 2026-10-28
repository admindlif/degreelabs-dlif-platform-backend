"""add checklist review history and persistent portal notifications

Revision ID: d3e4f5a6b7c8
Revises: c1d2e3f4a5b6
Create Date: 2026-10-27 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist_review_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checklist_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fellow_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mentor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "decision",
            postgresql.ENUM(
                "not_checked",
                "pending_review",
                "accepted",
                "rejected",
                name="checklist_review_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["completion_id"],
            ["fellow_checklist_completions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["checklist_item_id"], ["checklist_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["fellow_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mentor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checklist_review_events_completion_id",
        "checklist_review_events",
        ["completion_id"],
    )
    op.create_index(
        "ix_checklist_review_events_checklist_item_id",
        "checklist_review_events",
        ["checklist_item_id"],
    )
    op.create_index(
        "ix_checklist_review_events_fellow_user_id",
        "checklist_review_events",
        ["fellow_user_id"],
    )
    op.create_index(
        "ix_checklist_review_events_mentor_user_id",
        "checklist_review_events",
        ["mentor_user_id"],
    )
    op.create_index(
        "ix_checklist_review_events_created_at",
        "checklist_review_events",
        ["created_at"],
    )

    op.create_table(
        "portal_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portal_notifications_recipient_user_id",
        "portal_notifications",
        ["recipient_user_id"],
    )
    op.create_index(
        "ix_portal_notifications_notification_type",
        "portal_notifications",
        ["notification_type"],
    )
    op.create_index(
        "ix_portal_notifications_read_at", "portal_notifications", ["read_at"]
    )
    op.create_index(
        "ix_portal_notifications_created_at", "portal_notifications", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_portal_notifications_created_at", table_name="portal_notifications")
    op.drop_index("ix_portal_notifications_read_at", table_name="portal_notifications")
    op.drop_index(
        "ix_portal_notifications_notification_type", table_name="portal_notifications"
    )
    op.drop_index(
        "ix_portal_notifications_recipient_user_id", table_name="portal_notifications"
    )
    op.drop_table("portal_notifications")

    op.drop_index(
        "ix_checklist_review_events_created_at", table_name="checklist_review_events"
    )
    op.drop_index(
        "ix_checklist_review_events_mentor_user_id",
        table_name="checklist_review_events",
    )
    op.drop_index(
        "ix_checklist_review_events_fellow_user_id",
        table_name="checklist_review_events",
    )
    op.drop_index(
        "ix_checklist_review_events_checklist_item_id",
        table_name="checklist_review_events",
    )
    op.drop_index(
        "ix_checklist_review_events_completion_id",
        table_name="checklist_review_events",
    )
    op.drop_table("checklist_review_events")
