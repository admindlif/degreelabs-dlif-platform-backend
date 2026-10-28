"""add_phase_gate_assessments

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-10-28 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- phase_gate_assessments ---
    op.create_table(
        "phase_gate_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mentor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("phase_gate_code", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("total_cleared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_uncleared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mentor_notes", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "phase_gate_code", name="uq_phase_gate_assessment_team_gate"),
    )
    op.create_index("ix_phase_gate_assessments_team_id", "phase_gate_assessments", ["team_id"])
    op.create_index("ix_phase_gate_assessments_cohort_id", "phase_gate_assessments", ["cohort_id"])
    op.create_index("ix_phase_gate_assessments_mentor_user_id", "phase_gate_assessments", ["mentor_user_id"])
    op.create_index("ix_phase_gate_assessments_phase_gate_code", "phase_gate_assessments", ["phase_gate_code"])

    # --- phase_gate_dimension_results ---
    op.create_table(
        "phase_gate_dimension_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("phase_gate_assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension_key", sa.String(80), nullable=False),
        sa.Column("dimension_title", sa.String(200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("is_cleared", sa.Boolean(), nullable=True),
        sa.Column("mentor_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("assessment_id", "dimension_key", name="uq_gate_dimension_result"),
    )
    op.create_index("ix_phase_gate_dimension_results_assessment_id", "phase_gate_dimension_results", ["assessment_id"])


def downgrade() -> None:
    op.drop_table("phase_gate_dimension_results")
    op.drop_table("phase_gate_assessments")
