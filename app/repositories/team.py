from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.team import (
    Team,
    TeamMembership,
    TeamMemberRole,
)


def get_team_for_fellow(
    db: Session,
    user_id: UUID,
    cohort_id: UUID,
) -> Team | None:
    """
    Return the Team the Fellow belongs to within the given cohort,
    eagerly loading members and User records.
    """
    membership = (
        db.query(TeamMembership)
        .options(
            joinedload(TeamMembership.team)
            .joinedload(Team.members)
            .joinedload(TeamMembership.user)
        )
        .filter(
            TeamMembership.user_id == user_id,
            TeamMembership.cohort_id == cohort_id,
        )
        .first()
    )

    return membership.team if membership else None


def get_team_by_id(
    db: Session,
    team_id: UUID,
) -> Team | None:
    return db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )


def get_team_membership(
    db: Session,
    team_id: UUID,
    user_id: UUID,
) -> TeamMembership | None:
    return db.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
        )
    )


def get_team_membership_for_cohort(
    db: Session,
    cohort_id: UUID,
    user_id: UUID,
) -> TeamMembership | None:
    return db.scalar(
        select(TeamMembership).where(
            TeamMembership.cohort_id == cohort_id,
            TeamMembership.user_id == user_id,
        )
    )


def get_team_lead(
    db: Session,
    team_id: UUID,
) -> TeamMembership | None:
    return db.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.team_role == TeamMemberRole.LEAD,
        )
    )