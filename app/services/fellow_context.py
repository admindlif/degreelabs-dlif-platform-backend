from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.discover import get_phase_by_code
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.fellow_context import (
    CohortSummary,
    FellowContextResponse,
    FellowUserSummary,
    PhaseSummary,
    ProgramSummary,
)


def get_fellow_context(db: Session, current_user: User) -> FellowContextResponse:
    """
    Retrieve the program, cohort, and current phase context for an authenticated Fellow.
    """
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    cohort = enrollment.cohort
    program = cohort.program

    phase = get_phase_by_code(db, program.id, code="DISCOVER")
    if not phase:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active DISCOVER phase not found for current program.",
        )

    return FellowContextResponse(
        fellow=FellowUserSummary(
            id=current_user.id,
            first_name=current_user.first_name,
            last_name=current_user.last_name,
            email=current_user.email,
            role=current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
        ),
        program=ProgramSummary(
            id=program.id,
            name=program.name,
            code=program.code,
        ),
        cohort=CohortSummary(
            id=cohort.id,
            name=cohort.name,
            code=cohort.code,
            status=cohort.status.value if hasattr(cohort.status, "value") else str(cohort.status),
        ),
        current_phase=PhaseSummary(
            id=phase.id,
            code=phase.code,
            name=phase.name,
            development_role=phase.development_role,
            sequence=phase.sequence,
        ),
    )
