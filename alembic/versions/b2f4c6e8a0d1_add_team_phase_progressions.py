"""add_team_phase_progressions

Revision ID: b2f4c6e8a0d1
Revises: 61fa213d09bd
Create Date: 2026-10-24 14:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2f4c6e8a0d1"
down_revision: Union[str, Sequence[str], None] = "61fa213d09bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create team_phase_progressions table
    op.create_table(
        "team_phase_progressions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=False),
        sa.Column("phase_code", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'locked'"), nullable=False),
        sa.Column("decision", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cohort_id"], ["cohorts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "phase_code", name="uq_team_phase_progression"),
    )
    op.create_index(op.f("ix_team_phase_progressions_cohort_id"), "team_phase_progressions", ["cohort_id"], unique=False)
    op.create_index(op.f("ix_team_phase_progressions_phase_code"), "team_phase_progressions", ["phase_code"], unique=False)
    op.create_index(op.f("ix_team_phase_progressions_team_id"), "team_phase_progressions", ["team_id"], unique=False)

    # 2. Seed canonical VALIDATE and GROW phases for existing programs if not present
    conn = op.get_bind()
    programs = conn.execute(sa.text("SELECT id FROM programs")).fetchall()
    for row in programs:
        prog_id = row[0]
        # Check VALIDATE
        val_exists = conn.execute(
            sa.text("SELECT id FROM phases WHERE program_id = :p AND code = 'VALIDATE'"),
            {"p": prog_id},
        ).fetchone()
        if not val_exists:
            conn.execute(
                sa.text("""
                    INSERT INTO phases (id, program_id, code, name, development_role, sequence, duration_weeks, description, is_active, created_at, updated_at)
                    VALUES (:id, :p, 'VALIDATE', 'VALIDATE', 'PROVE', 2, 4, 'A 4-week proof and validation phase.', true, now(), now())
                """),
                {"id": str(uuid.uuid4()), "p": prog_id},
            )

        # Check GROW
        grow_exists = conn.execute(
            sa.text("SELECT id FROM phases WHERE program_id = :p AND code = 'GROW'"),
            {"p": prog_id},
        ).fetchone()
        if not grow_exists:
            conn.execute(
                sa.text("""
                    INSERT INTO phases (id, program_id, code, name, development_role, sequence, duration_weeks, description, is_active, created_at, updated_at)
                    VALUES (:id, :p, 'GROW', 'GROW', 'DELIVER', 3, 16, 'A multi-month paid implementation delivery phase.', true, now(), now())
                """),
                {"id": str(uuid.uuid4()), "p": prog_id},
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_team_phase_progressions_team_id"), table_name="team_phase_progressions")
    op.drop_index(op.f("ix_team_phase_progressions_phase_code"), table_name="team_phase_progressions")
    op.drop_index(op.f("ix_team_phase_progressions_cohort_id"), table_name="team_phase_progressions")
    op.drop_table("team_phase_progressions")
