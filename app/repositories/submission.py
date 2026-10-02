from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.submission import TeamSubmission


def get_team_submission_for_session(
    db: Session,
    team_id: UUID,
    session_id: UUID,
) -> TeamSubmission | None:
    """
    Return the Team's submission for one Session.
    """
    return db.scalar(
        select(TeamSubmission).where(
            TeamSubmission.team_id == team_id,
            TeamSubmission.session_id == session_id,
        )
    )