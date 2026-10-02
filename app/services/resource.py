from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.discover import get_phase_by_code
from app.repositories.enrollment import get_active_enrollment_for_user
from app.repositories.resource import (
    get_resources_for_phase,
    get_resources_for_session,
)
from app.schemas.team_resource import ResourceResponse
from uuid import UUID

from app.repositories.discover import (
    get_phase_by_code,
    get_session_by_id_for_cohort,
)
from app.services.discover import require_session_unlocked


def get_fellow_resources(db: Session, current_user: User) -> list[ResourceResponse]:
    """
    Return the active Resources for the Fellow's current phase (e.g., DISCOVER).
    Returns an empty list (not a 404) if no resources are seeded yet — the
    frontend will gracefully show the fallback toolkit items.
    """
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active enrollment found for this Fellow.",
        )

    cohort = enrollment.cohort
    program = cohort.program

    phase = get_phase_by_code(db, program.id, code="DISCOVER")
    if not phase:
        return []

    resources = get_resources_for_phase(db, phase.id)

    return [
        ResourceResponse(
            id=str(r.id),
            title=r.title,
            subtitle=r.subtitle,
            resource_type=r.resource_type.value,
            url=r.url,
            is_downloadable=r.is_downloadable,
            sequence=r.sequence,
        )
        for r in resources
    ]

def get_fellow_session_resources(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> list[ResourceResponse]:
    """
    Return resources for one Session.

    The Fellow must:
    - have an active Cohort enrollment,
    - belong to the Session's Cohort,
    - have access to an unlocked Session.
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

    # Parent Session lock protects all children.
    require_session_unlocked(session)

    resources = get_resources_for_session(
        db,
        session.id,
    )

    return [
        ResourceResponse(
            id=str(resource.id),
            title=resource.title,
            subtitle=resource.subtitle,
            resource_type=(
                resource.resource_type.value
                if hasattr(
                    resource.resource_type,
                    "value",
                )
                else str(
                    resource.resource_type
                )
            ),
            url=resource.url,
            is_downloadable=(
                resource.is_downloadable
            ),
            sequence=resource.sequence,
        )
        for resource in resources
    ]
