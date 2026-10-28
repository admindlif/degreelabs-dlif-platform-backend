from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProgramSessionOverview(Base):
    __tablename__ = "program_session_overviews"
    __table_args__ = (
        UniqueConstraint(
            "phase_code",
            "session_number",
            "source_version",
            name="uq_program_session_overview_phase_session_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    phase_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )  # e.g., DISCOVER, VALIDATE, GROW

    session_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )  # Canonical session number: 1..12

    purpose: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    what_to_explore: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    working_evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    source_document: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="DLIF Fellow Handbook – DISCOVER",
    )

    source_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="1.0",
    )

    source_page: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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
