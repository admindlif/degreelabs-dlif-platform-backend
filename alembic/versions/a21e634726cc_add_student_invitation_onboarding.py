"""add student invitation onboarding

Revision ID: a21e634726cc
Revises: bcd707e50dbe
Create Date: 2026-09-25 09:45:22.201462
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a21e634726cc"
down_revision: Union[str, Sequence[str], None] = "bcd707e50dbe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


account_status_enum = postgresql.ENUM(
    "invited",
    "active",
    "suspended",
    name="account_status",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""

    bind = op.get_bind()

    # PostgreSQL requires the enum type to exist before
    # a column can use it.
    account_status_enum.create(
        bind,
        checkfirst=True,
    )

    # Invitation tokens
    op.create_table(
        "user_invitation_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_user_invitation_tokens_token_hash",
        "user_invitation_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_index(
        "ix_user_invitation_tokens_user_id",
        "user_invitation_tokens",
        ["user_id"],
        unique=False,
    )

    # Add account lifecycle fields.
    #
    # Temporary defaults are necessary because the users table
    # may already contain records.
    op.add_column(
        "users",
        sa.Column(
            "account_status",
            account_status_enum,
            nullable=False,
            server_default="invited",
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "two_factor_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "password_set_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Existing users already had passwords before invitation-based
    # onboarding was introduced, so preserve them as active users.
    op.execute(
        """
        UPDATE users
        SET account_status = 'active'
        WHERE password_hash IS NOT NULL
        """
    )

    # Invited students are created before choosing a password.
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # The defaults above were only needed to safely migrate existing rows.
    # New values should come from application logic.
    op.alter_column(
        "users",
        "account_status",
        server_default=None,
    )

    op.alter_column(
        "users",
        "two_factor_enabled",
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""

    # WARNING:
    # This will only succeed if there are no users whose
    # password_hash is NULL.
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    op.drop_column(
        "users",
        "last_login_at",
    )

    op.drop_column(
        "users",
        "password_set_at",
    )

    op.drop_column(
        "users",
        "email_verified_at",
    )

    op.drop_column(
        "users",
        "two_factor_enabled",
    )

    op.drop_column(
        "users",
        "account_status",
    )

    op.drop_index(
        "ix_user_invitation_tokens_user_id",
        table_name="user_invitation_tokens",
    )

    op.drop_index(
        "ix_user_invitation_tokens_token_hash",
        table_name="user_invitation_tokens",
    )

    op.drop_table(
        "user_invitation_tokens"
    )

    # Enum must be dropped after the column using it is removed.
    account_status_enum.drop(
        op.get_bind(),
        checkfirst=True,
    )