from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.team import (
    TeamChallengeResource,
    TeamMembership,
    TeamMemberRole,
)
from app.models.user import User, UserRole
from app.repositories.enrollment import (
    get_active_enrollment_for_user,
)
from app.repositories.team import (
    get_team_by_id,
    get_team_for_fellow,
    get_team_lead,
    get_team_membership,
    get_team_membership_for_cohort,
)
from app.schemas.team_resource import (
    CompanyChallengeResponse,
    TeamChallengeResourceResponse,
    TeamMemberResponse,
    TeamResponse,
)


def get_fellow_team(
    db: Session,
    current_user: User,
) -> TeamResponse:
    """
    Return the Team for the authenticated Fellow
    in their active Cohort.
    """
    enrollment = get_active_enrollment_for_user(
        db,
        current_user.id,
    )

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No active enrollment found for this Fellow."
            ),
        )

    team = get_team_for_fellow(
        db,
        current_user.id,
        enrollment.cohort_id,
    )

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No team assignment found for this Fellow "
                "in the current cohort."
            ),
        )

    members: list[TeamMemberResponse] = []

    for membership in team.members:
        user = membership.user

        initials = (
            (user.first_name[0] if user.first_name else "")
            + (user.last_name[0] if user.last_name else "")
        ).upper()

        members.append(
            TeamMemberResponse(
                id=str(user.id),
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                initials=initials,
                team_role=membership.team_role.value,
            )
        )

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        company_challenge=team.company_challenge,
        company_name=team.company_name,
        member_count=len(members),
        members=members,
    )


def get_fellow_company_challenge(
    db: Session,
    current_user: User,
) -> CompanyChallengeResponse:
    """
    Return the Company Challenge assigned to the
    authenticated Fellow's Team in their active Cohort.
    """

    enrollment = get_active_enrollment_for_user(
        db,
        current_user.id,
    )

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No active enrollment found for this Fellow."
            ),
        )

    team = get_team_for_fellow(
        db,
        current_user.id,
        enrollment.cohort_id,
    )

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No team assignment found for this Fellow "
                "in the current cohort."
            ),
        )

    resources = db.scalars(
        select(TeamChallengeResource)
        .where(
            TeamChallengeResource.team_id
            == team.id
        )
        .order_by(
            TeamChallengeResource.sequence,
            TeamChallengeResource.created_at,
        )
    ).all()

    return CompanyChallengeResponse(
        team_id=team.id,
        team_name=team.name,
        company_name=team.company_name,
        company_overview=team.company_overview,
        company_challenge=team.company_challenge,
        challenge_description=(
            team.challenge_description
        ),
        resources=[
            TeamChallengeResourceResponse(
                id=resource.id,
                title=resource.title,
                resource_type=resource.resource_type,
                url=resource.url,
                is_downloadable=(
                    resource.is_downloadable
                ),
                sequence=resource.sequence,
            )
            for resource in resources
        ],
    )

def add_member_to_team(
    db: Session,
    team_id: UUID,
    user_id: UUID,
    team_role: TeamMemberRole = TeamMemberRole.MEMBER,
) -> TeamMembership:
    """
    Add a Fellow to a Team.

    Rules:
    - Team must exist and be active.
    - User must be a Fellow.
    - Fellow must have an active Cohort enrollment.
    - Fellow's active Cohort must equal Team's Cohort.
    - Fellow cannot already belong to another Team
      in the same Cohort.
    - A Team can have at most one Team Lead.
    """

    team = get_team_by_id(
        db,
        team_id,
    )

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found.",
        )

    if not team.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot add Fellows to an inactive Team.",
        )

    user = db.get(
        User,
        user_id,
    )

    if not user or user.role not in {
        UserRole.FELLOW,
        UserRole.STUDENT,
    }:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fellow not found.",
        )

    enrollment = get_active_enrollment_for_user(
        db,
        user_id,
    )

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Fellow must be actively enrolled "
                "in a Cohort before joining a Team."
            ),
        )

    if enrollment.cohort_id != team.cohort_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Fellow and Team must belong "
                "to the same Cohort."
            ),
        )

    existing_membership = (
        get_team_membership_for_cohort(
            db,
            team.cohort_id,
            user_id,
        )
    )

    if existing_membership:
        if existing_membership.team_id == team.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This Fellow is already a member "
                    "of this Team."
                ),
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This Fellow already belongs to another "
                "Team in this Cohort."
            ),
        )

    if team_role == TeamMemberRole.LEAD:
        existing_lead = get_team_lead(
            db,
            team.id,
        )

        if existing_lead:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This Team already has a Team Lead. "
                    "Use the change Team Lead action instead."
                ),
            )

    membership = TeamMembership(
        team_id=team.id,
        cohort_id=team.cohort_id,
        user_id=user_id,
        team_role=team_role,
    )

    db.add(membership)

    try:
        db.commit()
        db.refresh(membership)

    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Unable to add Fellow to Team because "
                "the membership conflicts with an existing "
                "Team assignment."
            ),
        ) from exc

    return membership


def set_team_lead(
    db: Session,
    team_id: UUID,
    user_id: UUID,
) -> TeamMembership:
    """
    Assign an existing Team member as Team Lead.

    Existing Team Lead is demoted to MEMBER first.
    """

    team = get_team_by_id(
        db,
        team_id,
    )

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found.",
        )

    membership = get_team_membership(
        db,
        team_id,
        user_id,
    )

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The selected Fellow must be a member "
                "of this Team before becoming Team Lead."
            ),
        )

    current_lead = get_team_lead(
        db,
        team_id,
    )

    if (
        current_lead
        and current_lead.user_id == user_id
    ):
        return membership

    try:
        # Flush the demotion first so the partial
        # unique index cannot conflict.
        if current_lead:
            current_lead.team_role = TeamMemberRole.MEMBER
            db.flush()

        membership.team_role = TeamMemberRole.LEAD

        db.commit()
        db.refresh(membership)

    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Unable to assign Team Lead because "
                "the Team already has a conflicting Lead."
            ),
        ) from exc

    return membership


def remove_member_from_team(
    db: Session,
    team_id: UUID,
    user_id: UUID,
) -> None:
    """
    Remove Fellow from Team.

    Current Team Lead must be replaced before removal
    while the Team is active.
    """

    team = get_team_by_id(
        db,
        team_id,
    )

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found.",
        )

    membership = get_team_membership(
        db,
        team_id,
        user_id,
    )

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team membership not found.",
        )

    if (
        team.is_active
        and membership.team_role == TeamMemberRole.LEAD
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Assign another Team Lead before removing "
                "the current Team Lead."
            ),
        )

    db.delete(membership)
    db.commit()