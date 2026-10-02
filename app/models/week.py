from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Week(Base):
    __tablename__ = "weeks"
    __table_args__ = (
        UniqueConstraint("phase_id", "week_number", name="uq_phase_week_number"),
        UniqueConstraint("phase_id", "sequence", name="uq_phase_week_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    phase_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("phases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    week_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # 1, 2, 3, 4

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )  # e.g., DISCOVER THE REAL PROBLEM

    strategic_question: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )  # e.g., "What is really happening here?"

    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unlock_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
    phase = relationship("Phase", back_populates="weeks")
    sessions = relationship("Session", back_populates="week", cascade="all, delete-orphan", order_by="Session.sequence")
