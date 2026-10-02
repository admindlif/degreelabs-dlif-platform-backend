from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.feedback import SubmissionFeedback
from app.models.user import User
from app.schemas.feedback import SubmissionFeedbackDetail
from app.services.submission import (
    get_fellow_session_submission,
)


def get_fellow_session_feedback(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> SubmissionFeedbackDetail | None:
    """
    Return feedback for the authenticated Fellow's
    Team submission for this Session.

    Authorization is inherited from the existing
    Team submission service.
    """

    submission_data = get_fellow_session_submission(
        db,
        current_user,
        session_id,
    )

    # No Team submission yet.
    if submission_data.submission is None:
        return None

    feedback = db.scalar(
        select(SubmissionFeedback).where(
            SubmissionFeedback.submission_id
            == submission_data.submission.id
        )
    )

    # Submission exists, but Admin has not reviewed it yet.
    if feedback is None:
        return None

    return SubmissionFeedbackDetail.model_validate(
        feedback
    )