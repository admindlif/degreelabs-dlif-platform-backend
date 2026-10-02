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
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ResourceType(str, Enum):
    HANDBOOK = "handbook"
    RUBRIC = "rubric"
    TEMPLATE = "template"
    GUIDE = "guide"
    LINK = "link"


class Resource(Base):
    """
    A curriculum resource available to Fellows in a given phase.
    Resources are scoped to a Phase (e.g., DISCOVER) and ordered by sequence.
    """

    __tablename__ = "resources"

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

    session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    subtitle: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )  # e.g. "PDF • v1.0 (Sept 2026)"

    resource_type: Mapped[ResourceType] = mapped_column(
        SQLEnum(
            ResourceType,
            name="resource_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=ResourceType.LINK,
    )

    url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    is_downloadable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
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
    phase = relationship("Phase", back_populates="resources")
    session = relationship("Session", back_populates="resources")
