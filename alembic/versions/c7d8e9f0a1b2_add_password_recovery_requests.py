"""add_password_recovery_requests

Revision ID: c7d8e9f0a1b2
Revises: b2f4c6e8a0d1
Create Date: 2026-10-27 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b2f4c6e8a0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_recovery_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portal",
            sa.String(length=50),
            nullable=False,
            server_default="fellow",
        ),
        sa.Column(
            "code_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "reset_token_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "reset_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_password_recovery_requests_user_id",
        "password_recovery_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_password_recovery_requests_reset_token_hash",
        "password_recovery_requests",
        ["reset_token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_password_recovery_requests_reset_token_hash",
        table_name="password_recovery_requests",
    )
    op.drop_index(
        "ix_password_recovery_requests_user_id",
        table_name="password_recovery_requests",
    )
    op.drop_table("password_recovery_requests")
