"""
Phase Gate Assessment models.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


DISCOVER_TO_VALIDATE_GATE = "DISCOVER_TO_VALIDATE"


class PhaseGateAssessment(Base):
    """
    Header record for a mentor's phase gate assessment of a team.
    One row per (team_id, phase_gate_code). Status: 'draft' or 'submitted'.
    """

    __tablename__ = "phase_gate_assessments"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "phase_gate_code",
            name="uq_phase_gate_assessment_team_gate",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mentor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phase_gate_code: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    total_cleared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_uncleared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mentor_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    team = relationship("Team", foreign_keys=[team_id])
    cohort = relationship("Cohort", foreign_keys=[cohort_id])
    mentor = relationship("User", foreign_keys=[mentor_user_id])
    dimension_results = relationship(
        "PhaseGateDimensionResult",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="PhaseGateDimensionResult.sequence",
    )


class PhaseGateDimensionResult(Base):
    """
    One row per dimension per assessment.
    5 DISCOVER to VALIDATE dimensions per DLIF Fellow Handbook Page 9.
    """

    __tablename__ = "phase_gate_dimension_results"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "dimension_key",
            name="uq_gate_dimension_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    assessment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("phase_gate_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension_key: Mapped[str] = mapped_column(String(80), nullable=False)
    dimension_title: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_cleared: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mentor_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    assessment = relationship("PhaseGateAssessment", back_populates="dimension_results")
