from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.phase import Phase
from app.models.user import AccountStatus, User
from app.repositories.enrollment import get_active_enrollment_for_user
from app.repositories.progression import get_team_phase_progressions
from app.repositories.team import get_team_for_fellow
from app.schemas.homepage import (
    FellowHomepageResponse,
    FellowHomepageUser,
    PhaseAccessVerificationResponse,
    PhaseRoadmapItem,
)


_PHASE_ROADMAP_METADATA = {
    "DISCOVER": {
        "title": "DISCOVER",
        "principle": "THINK",
        "duration": "4 weeks",
        "sequence": 1,
        "default_lock_reason": "Active cohort enrollment required.",
    },
    "VALIDATE": {
        "title": "VALIDATE",
        "principle": "PROVE",
        "duration": "4 weeks",
        "sequence": 2,
        "default_lock_reason": "Requires company shortlisting after DISCOVER.",
    },
    "GROW": {
        "title": "GROW",
        "principle": "DELIVER",
        "duration": "Typically 3–6 months",
        "sequence": 3,
        "default_lock_reason": "Requires successful VALIDATE and company approval for paid implementation.",
    },
}


def get_fellow_homepage(db: Session, current_user: User) -> FellowHomepageResponse:
    """
    Construct the authenticated Fellow's homepage with their safe display info
    and the 3-phase roadmap with database-driven access control.
    """
    # 1. Validate active user status
    if not current_user.is_active or current_user.account_status == AccountStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or suspended.",
        )

    # 2. Check cohort enrollment
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    is_enrolled = enrollment is not None

    team = None
    progressions_map = {}

    if is_enrolled and enrollment.cohort:
        team = get_team_for_fellow(db, current_user.id, enrollment.cohort_id)
        if team:
            team_progressions = get_team_phase_progressions(db, team.id)
            for prog in team_progressions:
                progressions_map[prog.phase_code.upper()] = prog

    # 3. Determine progression for each phase
    # DISCOVER: accessible to any active enrolled fellow
    discover_can_enter = is_enrolled
    discover_status = "unlocked" if discover_can_enter else "locked"
    discover_lock_reason = None if discover_can_enter else _PHASE_ROADMAP_METADATA["DISCOVER"]["default_lock_reason"]

    # VALIDATE: unlocked only when explicit company shortlisting is recorded
    validate_prog = progressions_map.get("VALIDATE")
    validate_can_enter = bool(
        is_enrolled
        and team
        and validate_prog
        and validate_prog.status.lower() == "approved"
    )
    validate_status = "unlocked" if validate_can_enter else "locked"
    validate_lock_reason = None if validate_can_enter else _PHASE_ROADMAP_METADATA["VALIDATE"]["default_lock_reason"]

    # GROW: unlocked only when VALIDATE is approved AND company approves paid implementation
    grow_prog = progressions_map.get("GROW")
    grow_can_enter = bool(
        validate_can_enter
        and grow_prog
        and grow_prog.status.lower() == "approved"
    )
    grow_status = "unlocked" if grow_can_enter else "locked"
    grow_lock_reason = None if grow_can_enter else _PHASE_ROADMAP_METADATA["GROW"]["default_lock_reason"]

    # 4. Read DB Phase definitions if available to enrich metadata
    db_phases_by_code = {}
    if is_enrolled and enrollment.cohort and enrollment.cohort.program_id:
        db_phases = db.scalars(
            select(Phase)
            .where(
                Phase.program_id == enrollment.cohort.program_id,
                Phase.is_active == True,
            )
            .order_by(Phase.sequence.asc())
        ).all()
        for p in db_phases:
            db_phases_by_code[p.code.upper()] = p

    def _build_phase_item(code: str, can_enter: bool, access_status: str, lock_reason: str | None) -> PhaseRoadmapItem:
        meta = _PHASE_ROADMAP_METADATA[code]
        db_phase = db_phases_by_code.get(code)
        
        title = db_phase.name if db_phase else meta["title"]
        principle = db_phase.development_role if db_phase else meta["principle"]
        duration = f"{db_phase.duration_weeks} weeks" if db_phase and code != "GROW" else meta["duration"]

        return PhaseRoadmapItem(
            code=code,
            title=title,
            principle=principle,
            duration=duration,
            access_status=access_status,
            can_enter=can_enter,
            lock_reason=lock_reason,
        )

    phase_items = [
        _build_phase_item("DISCOVER", discover_can_enter, discover_status, discover_lock_reason),
        _build_phase_item("VALIDATE", validate_can_enter, validate_status, validate_lock_reason),
        _build_phase_item("GROW", grow_can_enter, grow_status, grow_lock_reason),
    ]

    # Safe display name
    full_name = f"{current_user.first_name} {current_user.last_name}".strip()
    display_name = full_name if full_name else "Fellow"

    return FellowHomepageResponse(
        fellow=FellowHomepageUser(
            display_name=display_name,
            first_name=current_user.first_name,
            last_name=current_user.last_name,
            email=current_user.email,
            role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        ),
        phases=phase_items,
    )


def verify_fellow_phase_access(db: Session, current_user: User, phase_code: str) -> PhaseAccessVerificationResponse:
    """
    Enforce backend phase access authorization.
    Throws HTTP 403 Forbidden if the requested phase is locked for this Fellow.
    """
    homepage = get_fellow_homepage(db, current_user)
    target_code = phase_code.upper()

    for phase in homepage.phases:
        if phase.code.upper() == target_code:
            if not phase.can_enter:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Phase {target_code} is locked: {phase.lock_reason}",
                )
            return PhaseAccessVerificationResponse(
                phase=target_code,
                status="unlocked",
                principle=phase.principle,
                message=f"Access verified for {target_code} phase.",
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown fellowship phase: {phase_code}",
    )
