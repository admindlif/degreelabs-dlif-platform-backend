from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.submission import TeamSubmission
from app.models.team import TeamMemberRole
from app.models.user import User

from app.repositories.discover import (
    get_session_by_id_for_cohort,
)
from app.repositories.enrollment import (
    get_active_enrollment_for_user,
)
from app.repositories.submission import (
    get_team_submission_for_session,
)
from app.repositories.team import (
    get_team_membership_for_cohort,
)

from app.schemas.submission import (
    SessionSubmissionResponse,
    TeamSubmissionDetail,
    TeamSubmissionUpsertRequest,
)

from app.services.discover import (
    require_session_unlocked,
)


def _get_submission_context(
    db: Session,
    current_user: User,
    session_id: UUID,
):
    """
    Validate Fellow access to the Session and Team.
    """

    enrollment = get_active_enrollment_for_user(
        db,
        current_user.id,
    )

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No active enrollment found "
                "for this Fellow."
            ),
        )

    session = get_session_by_id_for_cohort(
        db,
        session_id,
        enrollment.cohort_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    # Parent Session lock protects Submission too.
    require_session_unlocked(
        session
    )

    membership = (
        get_team_membership_for_cohort(
            db,
            enrollment.cohort_id,
            current_user.id,
        )
    )

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No Team assignment found "
                "for this Fellow."
            ),
        )

    if not membership.team.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Fellow's Team is inactive.",
        )

    return session, membership


def get_fellow_session_submission(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> SessionSubmissionResponse:
    """
    Team Lead and Team Members may view
    their Team's Submission for an unlocked Session.
    """

    session, membership = (
        _get_submission_context(
            db,
            current_user,
            session_id,
        )
    )

    is_team_lead = (
        membership.team_role
        == TeamMemberRole.LEAD
    )

    submission = (
        get_team_submission_for_session(
            db,
            membership.team_id,
            session.id,
        )
    )

    return SessionSubmissionResponse(
        submission=(
            TeamSubmissionDetail.model_validate(
                submission
            )
            if submission
            else None
        ),
        is_team_lead=is_team_lead,
        can_submit=(
            session.submission_enabled
            and is_team_lead
        ),
    )


def upsert_fellow_session_submission(
    db: Session,
    current_user: User,
    session_id: UUID,
    data: TeamSubmissionUpsertRequest,
) -> SessionSubmissionResponse:
    """
    Create or replace a Team Submission.

    Only the Team Lead may submit/resubmit.
    """

    session, membership = (
        _get_submission_context(
            db,
            current_user,
            session_id,
        )
    )

    if not session.submission_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Submission is not enabled "
                "for this Session."
            ),
        )

    if (
        membership.team_role
        != TeamMemberRole.LEAD
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the Team Lead can submit "
                "or resubmit work."
            ),
        )

    drive_url = data.drive_url.strip()

    if not drive_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Submission link is required.",
        )

    submission = (
        get_team_submission_for_session(
            db,
            membership.team_id,
            session.id,
        )
    )

    now = datetime.now(
        timezone.utc
    )

    if submission:
        # Resubmission updates the same Team/Session record.
        submission.drive_url = drive_url

        submission.submitted_by_user_id = (
            current_user.id
        )

        submission.submitted_at = now

    else:
        submission = TeamSubmission(
            session_id=session.id,
            team_id=membership.team_id,

            submitted_by_user_id=(
                current_user.id
            ),

            drive_url=drive_url,
            submitted_at=now,
        )

        db.add(submission)

    db.commit()
    db.refresh(submission)

    return SessionSubmissionResponse(
        submission=(
            TeamSubmissionDetail.model_validate(
                submission
            )
        ),
        is_team_lead=True,
        can_submit=True,
    )
