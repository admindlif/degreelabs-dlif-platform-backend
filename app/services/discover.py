from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.session import Session as DBSession, SessionStatus
from app.models.user import User
from app.repositories.discover import (
    get_next_session_for_cohort,
    get_phase_by_code,
    get_session_by_id_for_cohort,
    get_sessions_for_cohort,
    get_weeks_for_phase,
)
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.discover import (
    DiscoverOverviewResponse,
    DiscoverProgress,
    DiscoverWeekResponse,
    SessionDetailResponse,
    SessionSummary,
)
from app.schemas.fellow_context import CohortSummary, PhaseSummary


def is_session_unlocked(
    session: DBSession,
) -> bool:
    """
    Master Session access switch.

    A Fellow can access Session-specific content only when
    an Admin has explicitly unlocked the Session.
    """
    return bool(session.is_unlocked)


def require_session_unlocked(
    session: DBSession,
) -> None:
    """
    Reject direct access to a locked Session.
    """
    if not is_session_unlocked(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This Session is locked. "
                "It must be unlocked by an Admin."
            ),
        )

def _to_session_summary(
    session: DBSession,
) -> SessionSummary:
    unlocked = is_session_unlocked(session)

    # ---------------------------------------------------------
    # LOCKED SESSION
    #
    # Fellows can know the Session exists, but nothing
    # Session-specific is exposed.
    # ---------------------------------------------------------
    if not unlocked:
        return SessionSummary(
            id=session.id,
            session_number=session.session_number,

            session_type=None,

            # Do not expose the real Session title yet.
            title=f"Session {session.session_number}",

            description=None,

            start_at=None,
            end_at=None,
            unlock_at=None,

            submission_enabled=False,

            meeting_url=None,
            recording_url=None,
            transcript_url=None,

            status="locked",
            sequence=session.sequence,

            is_unlocked=False,
            has_recording=False,
            has_transcript=False,
        )

    # ---------------------------------------------------------
    # UNLOCKED SESSION
    # ---------------------------------------------------------
    return SessionSummary(
        id=session.id,
        session_number=session.session_number,

        session_type=(
            session.session_type.value
            if hasattr(
                session.session_type,
                "value",
            )
            else str(session.session_type)
        ),

        title=session.title,
        description=session.description,

        start_at=session.start_at,
        end_at=session.end_at,
        unlock_at=session.unlock_at,

        submission_enabled=(
            session.submission_enabled
        ),

        meeting_url=session.meeting_url,
        recording_url=session.recording_url,
        transcript_url=session.transcript_url,

        status=(
            session.status.value
            if hasattr(session.status, "value")
            else str(session.status)
        ),

        sequence=session.sequence,

        is_unlocked=True,

        has_recording=bool(
            session.recording_url
        ),

        has_transcript=bool(
            session.transcript_url
        ),
    )
def get_fellow_sessions(
    db: Session,
    current_user: User,
) -> list[SessionSummary]:
    """
    Return every session assigned to the Fellow's active cohort.
    """

    enrollment = get_active_enrollment_for_user(
        db,
        current_user.id,
    )

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    sessions = db.scalars(
        select(DBSession)
        .where(
            DBSession.cohort_id
            == enrollment.cohort_id
        )
        .order_by(
            DBSession.sequence.asc(),
            DBSession.start_at.asc(),
        )
    ).all()

    return [
        _to_session_summary(session)
        for session in sessions
    ]

def get_discover_overview(db: Session, current_user: User) -> DiscoverOverviewResponse:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    cohort = enrollment.cohort
    phase = get_phase_by_code(db, cohort.program_id, code="DISCOVER")
    if not phase:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active DISCOVER phase not found.",
        )

    sessions = get_sessions_for_cohort(db, cohort.id, phase.id)
    curriculum_sessions = sorted(
        (
            session
            for session in sessions
            if 1 <= session.session_number <= 12
        ),
        key=lambda session: session.session_number,
    )
    total_sessions = len(curriculum_sessions)
    completed_sessions = sum(
        1
        for session in curriculum_sessions
        if session.status == SessionStatus.COMPLETED
    )
    percentage = (
        round((completed_sessions / total_sessions) * 100)
        if total_sessions > 0
        else 0
    )

    next_session_db = get_next_session_for_cohort(db, cohort.id, phase.id)
    next_session_summary = _to_session_summary(next_session_db) if next_session_db else None

    # Curriculum progress is independent from the operational next Session.
    current_week = 1
    first_incomplete_session = next(
        (
            session
            for session in curriculum_sessions
            if session.status != SessionStatus.COMPLETED
        ),
        None,
    )
    if first_incomplete_session and first_incomplete_session.week:
        current_week = first_incomplete_session.week.week_number
    elif total_sessions == 12 and completed_sessions == 12:
        current_week = phase.duration_weeks

    progress = DiscoverProgress(
        current_week=current_week,
        total_weeks=phase.duration_weeks,
        percentage=percentage,
        completed_sessions=completed_sessions,
        total_sessions=total_sessions,
    )

    return DiscoverOverviewResponse(
        phase=PhaseSummary(
            id=phase.id,
            code=phase.code,
            name=phase.name,
            development_role=phase.development_role,
            sequence=phase.sequence,
        ),
        cohort=CohortSummary(
            id=cohort.id,
            name=cohort.name,
            code=cohort.code,
            status=cohort.status.value if hasattr(cohort.status, "value") else str(cohort.status),
        ),
        progress=progress,
        next_session=next_session_summary,
    )


def get_discover_weeks(db: Session, current_user: User) -> list[DiscoverWeekResponse]:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    cohort = enrollment.cohort
    phase = get_phase_by_code(db, cohort.program_id, code="DISCOVER")
    if not phase:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active DISCOVER phase not found.",
        )

    weeks = get_weeks_for_phase(db, phase.id)
    sessions = get_sessions_for_cohort(db, cohort.id, phase.id)

    # Map sessions to weeks. If week_id is None (e.g. Session 0), attach it to Week 1
    week_sessions_map: dict[UUID, list] = {w.id: [] for w in weeks}
    unassigned_sessions = []

    for s in sessions:
        if s.week_id and s.week_id in week_sessions_map:
            week_sessions_map[s.week_id].append(s)
        else:
            unassigned_sessions.append(s)

    # Attach unassigned (induction Session 0) to Week 1 if available
    if weeks and unassigned_sessions:
        first_week_id = weeks[0].id
        week_sessions_map[first_week_id] = sorted(
            unassigned_sessions + week_sessions_map[first_week_id],
            key=lambda x: x.sequence,
        )

    # Find the current active week
    next_session_db = get_next_session_for_cohort(db, cohort.id, phase.id)
    active_week_number = 1
    if next_session_db and next_session_db.week:
        active_week_number = next_session_db.week.week_number

    results = []
    for w in weeks:
        w_sessions = [_to_session_summary(s) for s in week_sessions_map.get(w.id, [])]
        
        # Determine status
        if w.week_number < active_week_number:
            week_status = "completed"
            status_badge = "Completed"
        elif w.week_number == active_week_number:
            week_status = "active"
            status_badge = "Current Week • In Progress"
        elif w.week_number == active_week_number + 1:
            week_status = "upcoming"
            status_badge = "Upcoming"
        else:
            week_status = "locked"
            status_badge = "Locked"

        results.append(
            DiscoverWeekResponse(
                id=w.id,
                week_number=w.week_number,
                title=w.title,
                strategic_question=w.strategic_question,
                description=w.description,
                sequence=w.sequence,
                status=week_status,
                status_badge=status_badge,
                sessions=w_sessions,
            )
        )

    return results


def get_session_detail(db: Session, current_user: User, session_id: UUID) -> SessionDetailResponse:
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
    if not is_session_unlocked(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This Session is locked and is not yet available."
            ),
        )   

    return SessionDetailResponse(
        id=session.id,
        cohort_id=session.cohort_id,
        phase_id=session.phase_id,
        week_id=session.week_id,

        session_number=session.session_number,

        session_type=(
            session.session_type.value
            if hasattr(
                session.session_type,
                "value",
            )
            else str(session.session_type)
        ),

        title=session.title,
        description=session.description,

        start_at=session.start_at,
        end_at=session.end_at,
        unlock_at=session.unlock_at,

        submission_enabled=(
            session.submission_enabled
        ),

        meeting_url=session.meeting_url,
        recording_url=session.recording_url,
        transcript_url=session.transcript_url,

        status=(
            session.status.value
            if hasattr(session.status, "value")
            else str(session.status)
        ),

        sequence=session.sequence,

        is_unlocked=True,

        has_recording=bool(
            session.recording_url
        ),

        has_transcript=bool(
            session.transcript_url
        ),

        week_title=(
            session.week.title
            if session.week
            else None
        ),

        week_number=(
            session.week.week_number
            if session.week
            else None
        ),
    )
