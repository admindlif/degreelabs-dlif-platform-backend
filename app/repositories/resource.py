from uuid import UUID

from sqlalchemy.orm import Session

from app.models.resource import Resource


def get_resources_for_phase(
    db: Session,
    phase_id: UUID,
) -> list[Resource]:
    """
    Return only global phase-level resources.

    Session-specific resources must not appear
    in the Fellow Toolkit.
    """
    return (
        db.query(Resource)
        .filter(
            Resource.phase_id == phase_id,
            Resource.session_id.is_(None),
            Resource.is_active == True,  # noqa: E712
        )
        .order_by(Resource.sequence)
        .all()
    )


def get_resources_for_session(
    db: Session,
    session_id: UUID,
) -> list[Resource]:
    """
    Return active resources attached to one Session.
    """
    return (
        db.query(Resource)
        .filter(
            Resource.session_id == session_id,
            Resource.is_active == True,  # noqa: E712
        )
        .order_by(Resource.sequence)
        .all()
    )