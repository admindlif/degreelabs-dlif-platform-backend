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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ChecklistReviewStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# ChecklistItem
# ---------------------------------------------------------------------------


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    cohort_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # NULL preserves legacy Admin-created global/cohort/phase definitions.
    # Mentor-created definitions are always restricted to exactly one team.
    team_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phase_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("phases.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    week_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("weeks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Canonical session number (e.g. 9).  When set, this item is visible for
    # every session whose session_number matches, regardless of cohort.
    # Mutually exclusive with session_id in practice; set one or the other.
    session_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # System-defined items (seeded, not Admin/Mentor created) are protected
    # from modification or deletion via CRUD APIs.
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Optional: explicit link to the document (TeamSubmission) this item needs.
    # NULL means the item is not document-gated.
    required_submission_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    action_label: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    action_url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
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

    completions: Mapped[list["FellowChecklistCompletion"]] = relationship(
        "FellowChecklistCompletion",
        back_populates="checklist_item",
        cascade="all, delete-orphan",
    )
    feedback_messages: Mapped[list["ChecklistFeedbackMessage"]] = relationship(
        "ChecklistFeedbackMessage",
        back_populates="checklist_item",
        cascade="all, delete-orphan",
    )
    team = relationship("Team", foreign_keys=[team_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])


# ---------------------------------------------------------------------------
# FellowChecklistCompletion  (extended)
# ---------------------------------------------------------------------------


class FellowChecklistCompletion(Base):
    """
    Individual fellow's response to a checklist item.

    review_status lifecycle:
        not_checked  →  (fellow checks)  →  pending_review
        pending_review  →  (mentor accepts)  →  accepted
        pending_review  →  (mentor rejects)  →  rejected
        rejected  →  (fellow re-checks/updates)  →  pending_review
    """

    __tablename__ = "fellow_checklist_completions"
    __table_args__ = (
        UniqueConstraint(
            "checklist_item_id",
            "user_id",
            name="uq_checklist_item_user_completion",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    checklist_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ----- legacy boolean (preserved for backward compat) -----
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ----- fellow's written responses (modal) -----
    contribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    my_thoughts: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- review lifecycle -----
    review_status: Mapped[ChecklistReviewStatus] = mapped_column(
        SQLEnum(
            ChecklistReviewStatus,
            name="checklist_review_status",
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
        default=ChecklistReviewStatus.NOT_CHECKED,
        server_default=text("'not_checked'"),
        index=True,
    )

    # Mentor who performed the last review action
    reviewed_by_mentor_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Mentor's rejection reason (shown to the fellow)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    checklist_item: Mapped[ChecklistItem] = relationship(
        "ChecklistItem",
        back_populates="completions",
    )
    fellow: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[user_id],
    )
    reviewer: Mapped["User | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[reviewed_by_mentor_id],
    )

    review_events: Mapped[list["ChecklistReviewEvent"]] = relationship(
        "ChecklistReviewEvent",
        back_populates="completion",
        cascade="all, delete-orphan",
    )


class ChecklistReviewEvent(Base):
    """Append-only audit record for every mentor checklist decision."""

    __tablename__ = "checklist_review_events"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    completion_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("fellow_checklist_completions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checklist_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fellow_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mentor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    decision: Mapped[ChecklistReviewStatus] = mapped_column(
        SQLEnum(
            ChecklistReviewStatus,
            name="checklist_review_status",
            values_callable=lambda e: [i.value for i in e],
            create_type=False,
        ),
        nullable=False,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    completion: Mapped[FellowChecklistCompletion] = relationship(
        "FellowChecklistCompletion",
        back_populates="review_events",
    )


# ---------------------------------------------------------------------------
# ChecklistFeedbackMessage  (new – thread per fellow+item)
# ---------------------------------------------------------------------------


class ChecklistFeedbackMessage(Base):
    """
    Threaded discussion between a Fellow and a Mentor about a specific
    checklist item.  One thread per (checklist_item, fellow).
    Messages are append-only; previous messages are never overwritten.
    """

    __tablename__ = "checklist_feedback_messages"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    checklist_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The fellow whose checklist item this thread belongs to
    fellow_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Author of this specific message (may be fellow OR mentor)
    author_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Author role snapshot (stored so it never changes even if user role changes)
    author_role: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    checklist_item: Mapped[ChecklistItem] = relationship(
        "ChecklistItem",
        back_populates="feedback_messages",
    )
    author: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[author_user_id],
    )
    fellow: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[fellow_user_id],
    )


# ---------------------------------------------------------------------------
# MentorTeamAssignment  (new)
# ---------------------------------------------------------------------------


class MentorTeamAssignment(Base):
    """
    Admin-managed assignment of a Mentor to a Team within a Cohort.
    A mentor may be assigned to multiple teams; a team may have multiple mentors.
    """

    __tablename__ = "mentor_team_assignments"
    __table_args__ = (
        UniqueConstraint(
            "mentor_user_id",
            "team_id",
            name="uq_mentor_team",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    mentor_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    mentor: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User",
        foreign_keys=[mentor_user_id],
    )
    team: Mapped["Team"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Team",
        back_populates="mentor_assignments",
    )
    cohort: Mapped["Cohort"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Cohort",
    )
