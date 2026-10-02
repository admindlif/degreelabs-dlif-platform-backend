from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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


class Phase(Base):
    __tablename__ = "phases"
    __table_args__ = (
        UniqueConstraint("program_id", "code", name="uq_program_phase_code"),
        UniqueConstraint("program_id", "sequence", name="uq_program_phase_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    program_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # e.g., DISCOVER, VALIDATE, GROW

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )  # e.g., DISCOVER

    development_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # e.g., THINK, PROVE, DELIVER

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # 1, 2, 3

    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    duration_weeks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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
    program = relationship("Program", back_populates="phases")
    weeks = relationship("Week", back_populates="phase", cascade="all, delete-orphan", order_by="Week.sequence")
    sessions = relationship("Session", back_populates="phase", cascade="all, delete-orphan")
    resources = relationship("Resource", back_populates="phase", cascade="all, delete-orphan", order_by="Resource.sequence")
