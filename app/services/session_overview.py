from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.program_session_overview import ProgramSessionOverview
from app.models.user import User
from app.repositories.discover import get_session_by_id_for_cohort
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.session_overview import SessionOverviewResponse
from app.services.discover import require_session_unlocked


def get_fellow_session_overview(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> SessionOverviewResponse:
    """
    Get the canonical global session overview for an authenticated Fellow.
    Resolves the canonical phase_code and session_number from the cohort session,
    then fetches the active, approved overview from the global table.
    """
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    session = get_session_by_id_for_cohort(db, session_id, enrollment.cohort_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    require_session_unlocked(session)

    # Session 0 is induction; the handbook does not define a 3-section overview for it.
    if session.session_number == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No session overview available for Session 0 Induction.",
        )

    phase_code = session.phase.code if session.phase else "DISCOVER"

    overview = db.scalars(
        select(ProgramSessionOverview)
        .where(
            ProgramSessionOverview.phase_code == phase_code,
            ProgramSessionOverview.session_number == session.session_number,
            ProgramSessionOverview.is_active.is_(True),
        )
        .order_by(ProgramSessionOverview.source_version.desc())
    ).first()

    if not overview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No approved session overview found for {phase_code} session {session.session_number}.",
        )

    return SessionOverviewResponse(
        phase_code=overview.phase_code,
        session_number=overview.session_number,
        purpose=overview.purpose,
        what_to_explore=overview.what_to_explore,
        working_evidence=overview.working_evidence,
        source_document=overview.source_document,
        source_version=overview.source_version,
        source_page=overview.source_page,
    )
