"""add_email_otp_challenges

Revision ID: e1f2a3b4c5d6
Revises: 8df6b766cca1
Create Date: 2026-10-29 12:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "8df6b766cca1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add preferred_auth_method column to users table
    op.add_column(
        "users",
        sa.Column(
            "preferred_auth_method",
            sa.String(length=32),
            nullable=True,
        ),
    )

    # 2. Create email_otp_challenges table
    op.create_table(
        "email_otp_challenges",
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
            "email",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            sa.String(length=50),
            nullable=False,
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
            "resend_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
            "ip_address",
            sa.String(length=45),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_email_otp_challenges_user_id",
        "email_otp_challenges",
        ["user_id"],
    )
    op.create_index(
        "ix_email_otp_challenges_purpose",
        "email_otp_challenges",
        ["purpose"],
    )
    op.create_index(
        "ix_email_otp_challenges_expires_at",
        "email_otp_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_otp_challenges_expires_at",
        table_name="email_otp_challenges",
    )
    op.drop_index(
        "ix_email_otp_challenges_purpose",
        table_name="email_otp_challenges",
    )
    op.drop_index(
        "ix_email_otp_challenges_user_id",
        table_name="email_otp_challenges",
    )
    op.drop_table("email_otp_challenges")
    op.drop_column("users", "preferred_auth_method")

