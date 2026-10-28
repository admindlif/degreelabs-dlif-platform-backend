"""add session_number and is_system to checklist_items

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-10-27 22:30:00.000000

Adds two new columns to checklist_items:
  - session_number (nullable integer): allows a checklist item to target every
    session with that session_number across all cohorts, without being tied to
    a specific session UUID.  Used for global, system-defined items (e.g. the
    two constant Session 9 tasks).
  - is_system (boolean, default false): marks checklist items that are
    system-defined and must not be edited or deleted via the Admin/Mentor CRUD
    APIs.

Also adds an index on session_number for efficient lookups.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checklist_items",
        sa.Column(
            "session_number",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_checklist_items_session_number",
        "checklist_items",
        ["session_number"],
        unique=False,
    )

    op.add_column(
        "checklist_items",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_items_session_number", table_name="checklist_items")
    op.drop_column("checklist_items", "session_number")
    op.drop_column("checklist_items", "is_system")
