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


class WorkingBoard(Base):
    __tablename__ = "working_boards"

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "team_id",
            name="uq_session_team_working_board",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    team_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        default="Working Board",
    )

    working_board_url: Mapped[str] = mapped_column(
        String(1500),
        nullable=False,
    )

    instructions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session")  # type: ignore[name-defined] # noqa: F821
    team: Mapped["Team"] = relationship("Team")  # type: ignore[name-defined] # noqa: F821
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_user_id])  # type: ignore[name-defined] # noqa: F821
    updated_by: Mapped["User | None"] = relationship("User", foreign_keys=[updated_by_user_id])  # type: ignore[name-defined] # noqa: F821
    insights: Mapped["WorkingBoardInsights | None"] = relationship(
        "WorkingBoardInsights",
        back_populates="working_board",
        uselist=False,
        cascade="all, delete-orphan",
    )


class WorkingBoardInsights(Base):
    __tablename__ = "working_board_insights"

    __table_args__ = (
        UniqueConstraint(
            "working_board_id",
            name="uq_working_board_insights_board",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    working_board_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("working_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    mentor_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    insights_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    publication_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    working_board: Mapped[WorkingBoard] = relationship(
        "WorkingBoard",
        back_populates="insights",
    )
    mentor: Mapped["User"] = relationship("User", foreign_keys=[mentor_user_id])  # type: ignore[name-defined] # noqa: F821
    revisions: Mapped[list["WorkingBoardInsightRevision"]] = relationship(
        "WorkingBoardInsightRevision",
        back_populates="insight",
        cascade="all, delete-orphan",
        order_by="WorkingBoardInsightRevision.revision_number.asc()",
    )


class WorkingBoardInsightRevision(Base):
    __tablename__ = "working_board_insight_revisions"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    insight_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("working_board_insights.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    working_board_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("working_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    mentor_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    insights_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    publication_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    revision_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    insight: Mapped[WorkingBoardInsights] = relationship(
        "WorkingBoardInsights",
        back_populates="revisions",
    )
    mentor: Mapped["User"] = relationship("User", foreign_keys=[mentor_user_id])  # type: ignore[name-defined] # noqa: F821
