from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.progression import TeamPhaseProgression


def get_team_phase_progressions(db: Session, team_id: UUID) -> list[TeamPhaseProgression]:
    """Retrieve all phase progression records for a team."""
    stmt = (
        select(TeamPhaseProgression)
        .where(TeamPhaseProgression.team_id == team_id)
        .order_by(TeamPhaseProgression.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def get_team_phase_progression(
    db: Session, team_id: UUID, phase_code: str
) -> TeamPhaseProgression | None:
    """Retrieve progression for a specific phase for a team."""
    stmt = select(TeamPhaseProgression).where(
        TeamPhaseProgression.team_id == team_id,
        TeamPhaseProgression.phase_code == phase_code.upper(),
    )
    return db.scalars(stmt).first()


def upsert_team_phase_progression(
    db: Session,
    team_id: UUID,
    cohort_id: UUID,
    phase_code: str,
    status: str,
    decision: str | None = None,
    decided_by_user_id: UUID | None = None,
    notes: str | None = None,
) -> TeamPhaseProgression:
    """
    Create or update a canonical phase progression approval for a team.
    """
    code_upper = phase_code.upper()
    progression = get_team_phase_progression(db, team_id, code_upper)

    now = datetime.now(timezone.utc)

    if progression is None:
        progression = TeamPhaseProgression(
            team_id=team_id,
            cohort_id=cohort_id,
            phase_code=code_upper,
            status=status,
            decision=decision,
            decided_at=now if decided_by_user_id else None,
            decided_by_user_id=decided_by_user_id,
            notes=notes,
        )
        db.add(progression)
    else:
        progression.status = status
        progression.decision = decision
        progression.decided_at = now if decided_by_user_id else progression.decided_at
        if decided_by_user_id:
            progression.decided_by_user_id = decided_by_user_id
        if notes is not None:
            progression.notes = notes

    db.commit()
    db.refresh(progression)
    return progression
