from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TeamPhaseProgression(Base):
    """
    Canonical source of truth for fellowship phase progression approvals.

    Stores explicit company decisions (shortlisting after DISCOVER, paid implementation
    approval after VALIDATE) recorded by authorized administrators/program managers.
    """

    __tablename__ = "team_phase_progressions"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "phase_code",
            name="uq_team_phase_progression",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
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

    phase_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )  # e.g., "VALIDATE", "GROW"

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="locked",
    )  # e.g., "locked", "in_review", "approved", "rejected"

    decision: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )  # e.g., "shortlisted", "paid_implementation_approved", "rejected", "revoked"

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    team = relationship(
        "Team",
        back_populates="phase_progressions",
    )

    cohort = relationship(
        "Cohort",
    )

    decided_by = relationship(
        "User",
    )
