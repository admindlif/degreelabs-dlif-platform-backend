from uuid import UUID
from sqlalchemy import asc, select
from sqlalchemy.orm import Session, joinedload

from app.models.phase import Phase
from app.models.session import Session as DBSession, SessionStatus
from app.models.week import Week


def get_phase_by_code(db: Session, program_id: UUID, code: str = "DISCOVER") -> Phase | None:
    stmt = select(Phase).where(
        Phase.program_id == program_id,
        Phase.code == code,
        Phase.is_active == True,
    )
    return db.scalars(stmt).first()


def get_weeks_for_phase(db: Session, phase_id: UUID) -> list[Week]:
    stmt = (
        select(Week)
        .where(Week.phase_id == phase_id)
        .order_by(asc(Week.sequence))
    )
    return list(db.scalars(stmt).all())


def get_sessions_for_cohort(
    db: Session, cohort_id: UUID, phase_id: UUID
) -> list[DBSession]:
    stmt = (
        select(DBSession)
        .where(
            DBSession.cohort_id == cohort_id,
            DBSession.phase_id == phase_id,
        )
        .order_by(asc(DBSession.sequence))
    )
    return list(db.scalars(stmt).all())


def get_session_by_id_for_cohort(
    db: Session, session_id: UUID, cohort_id: UUID
) -> DBSession | None:
    stmt = (
        select(DBSession)
        .options(joinedload(DBSession.week))
        .where(
            DBSession.id == session_id,
            DBSession.cohort_id == cohort_id,
        )
    )
    return db.scalars(stmt).first()


def get_next_session_for_cohort(
    db: Session, cohort_id: UUID, phase_id: UUID
) -> DBSession | None:
    """
    Find the currently live session, or the first upcoming/scheduled session.
    """
    # 1. First check if any session is LIVE
    live_stmt = (
        select(DBSession)
        .where(
            DBSession.cohort_id == cohort_id,
            DBSession.phase_id == phase_id,
            DBSession.status == SessionStatus.LIVE,
        )
        .order_by(asc(DBSession.sequence))
    )
    live_session = db.scalars(live_stmt).first()
    if live_session:
        return live_session

    # 2. Otherwise find the first SCHEDULED session
    sched_stmt = (
        select(DBSession)
        .where(
            DBSession.cohort_id == cohort_id,
            DBSession.phase_id == phase_id,
            DBSession.status == SessionStatus.SCHEDULED,
        )
        .order_by(asc(DBSession.sequence))
    )
    return db.scalars(sched_stmt).first()
