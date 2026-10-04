from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.db.base import Base


class TeamMemberRole(str, Enum):
    LEAD = "lead"
    MEMBER = "member"


class Team(Base):
    """
    A DLIF Fellow team assigned to a specific company challenge within a cohort.
    Team names follow the DLIF convention (e.g., 'Alpha-4').
    """

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("cohort_id", "name", name="uq_cohort_team_name"),
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

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )  # e.g. "Alpha-4"

    company_challenge: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )  # e.g. "SK Innovation AI Diagnostics"

    company_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )  # e.g. "SK Innovation"

    company_overview: Mapped[str | None] = mapped_column(
        String(3000),
        nullable=True,
    )

    challenge_description: Mapped[str | None] = mapped_column(
        String(5000),
        nullable=True,
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
    cohort = relationship("Cohort", back_populates="teams")
    members = relationship(
        "TeamMembership",
        back_populates="team",
        cascade="all, delete-orphan",
    )

    submissions = relationship(
        "TeamSubmission",
        back_populates="team",
        cascade="all, delete-orphan",
    )

    challenge_resources = relationship(
        "TeamChallengeResource",
        back_populates="team",
        cascade="all, delete-orphan",
    )


class TeamMembership(Base):
    """
    Associates a Fellow (User) to a Team within a cohort.
    A Fellow can only be in one team per cohort.
    """

    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "user_id",
            name="uq_team_user_membership",
        ),
        UniqueConstraint(
            "cohort_id",
            "user_id",
            name="uq_cohort_user_team",
        ),
        Index(
            "uq_team_single_lead",
            "team_id",
            unique=True,
            postgresql_where=text(
                "team_role = 'lead'"
            ),
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

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    team_role: Mapped[TeamMemberRole] = mapped_column(
        SQLEnum(
            TeamMemberRole,
            name="team_member_role",
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=TeamMemberRole.MEMBER,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    team = relationship(
        "Team",
        back_populates="members",
    )

    user = relationship(
        "User",
        backref=backref(
            "team_memberships",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
    )


class TeamChallengeResource(Base):
    """
    Reference material attached to a Team's company challenge.

    V1 supports external links such as Google Drive/Docs.
    File storage metadata is included so direct uploads can later
    be backed by object storage such as Cloudflare R2.
    """

    __tablename__ = "team_challenge_resources"

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

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="link",
    )

    url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    storage_key: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    file_name: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    is_downloadable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
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

    team = relationship(
        "Team",
        back_populates="challenge_resources",
    )
