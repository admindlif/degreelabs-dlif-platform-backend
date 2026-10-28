"""add server-managed authentication sessions

Revision ID: 4c2d8e1f6a90
Revises: 8df6b766cca1
Create Date: 2026-10-28

The migration is additive. Existing users and existing application data are
not modified. Existing access JWTs remain compatible during the controlled
rollout window configured by the application.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "4c2d8e1f6a90"
down_revision: Union[str, Sequence[str], None] = "8df6b766cca1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("refresh_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address_hash", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_user_active",
        "auth_sessions",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "auth_session_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reuse_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["replaced_by_token_id"],
            ["auth_session_refresh_tokens.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "generation",
            name="uq_auth_session_refresh_token_generation",
        ),
    )
    op.create_index(
        "ix_auth_session_refresh_tokens_session_id",
        "auth_session_refresh_tokens",
        ["session_id"],
    )
    op.create_index(
        "ix_auth_session_refresh_tokens_token_hash",
        "auth_session_refresh_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "auth_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=True),
        sa.Column("ip_address_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_audit_events_user_occurred",
        "auth_audit_events",
        ["user_id", "occurred_at"],
    )
    op.create_index(
        "ix_auth_audit_events_type_occurred",
        "auth_audit_events",
        ["event_type", "occurred_at"],
    )

    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action", "key_hash", name="uq_auth_rate_limit_action_key"),
    )
    op.create_index(
        "ix_auth_rate_limit_updated_at",
        "auth_rate_limit_buckets",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limit_updated_at", table_name="auth_rate_limit_buckets")
    op.drop_table("auth_rate_limit_buckets")

    op.drop_index("ix_auth_audit_events_type_occurred", table_name="auth_audit_events")
    op.drop_index("ix_auth_audit_events_user_occurred", table_name="auth_audit_events")
    op.drop_table("auth_audit_events")

    op.drop_index(
        "ix_auth_session_refresh_tokens_token_hash",
        table_name="auth_session_refresh_tokens",
    )
    op.drop_index(
        "ix_auth_session_refresh_tokens_session_id",
        table_name="auth_session_refresh_tokens",
    )
    op.drop_table("auth_session_refresh_tokens")

    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
