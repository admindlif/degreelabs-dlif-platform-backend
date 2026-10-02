from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SessionType(str, Enum):
    INDUCTION = "induction"
    LEARN_WORK = "learn_work"
    OUTPUT_REVIEW = "output_review"


class SessionStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("cohort_id", "session_number", name="uq_cohort_session_number"),
        UniqueConstraint("cohort_id", "sequence", name="uq_cohort_session_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    phase_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("phases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    week_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("weeks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    session_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # 0 for induction; 1+ includes canonical and Admin-added Sessions

    session_type: Mapped[SessionType] = mapped_column(
        SQLEnum(
            SessionType,
            name="session_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SessionType.LEARN_WORK,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    unlock_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    is_unlocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    submission_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    meeting_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    meeting_provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    google_meet_space_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    google_meet_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    google_calendar_event_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    google_calendar_event_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    recording_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    transcript_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    status: Mapped[SessionStatus] = mapped_column(
        SQLEnum(
            SessionStatus,
            name="session_status",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SessionStatus.SCHEDULED,
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
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
    cohort = relationship("Cohort", back_populates="sessions")
    phase = relationship("Phase", back_populates="sessions")
    week = relationship("Week", back_populates="sessions")
    resources = relationship(
        "Resource",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Resource.sequence",
    )
    submissions = relationship(
        "TeamSubmission",
        back_populates="session",
        cascade="all, delete-orphan",
    )
