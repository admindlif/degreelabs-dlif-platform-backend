from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TeamSubmission(Base):
    __tablename__ = "team_submissions"

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "team_id",
            name="uq_session_team_submission",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "teams.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    drive_url: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )

    submitted_at: Mapped[datetime] = mapped_column(
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

    session = relationship(
        "Session",
        back_populates="submissions",
    )

    team = relationship(
        "Team",
        back_populates="submissions",
    )

    submitted_by = relationship(
        "User",
    )

    feedback = relationship(
        "SubmissionFeedback",
        back_populates="submission",
        cascade="all, delete-orphan",
        uselist=False,
    )