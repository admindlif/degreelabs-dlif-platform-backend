"""add team scope and creator to checklist items

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-27 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checklist_items",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "checklist_items",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_checklist_items_team_id_teams",
        "checklist_items",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_checklist_items_created_by_user_id_users",
        "checklist_items",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_checklist_items_team_id", "checklist_items", ["team_id"])
    op.create_index(
        "ix_checklist_items_created_by_user_id",
        "checklist_items",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_checklist_items_created_by_user_id", table_name="checklist_items")
    op.drop_index("ix_checklist_items_team_id", table_name="checklist_items")
    op.drop_constraint(
        "fk_checklist_items_created_by_user_id_users",
        "checklist_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_checklist_items_team_id_teams",
        "checklist_items",
        type_="foreignkey",
    )
    op.drop_column("checklist_items", "created_by_user_id")
    op.drop_column("checklist_items", "team_id")
