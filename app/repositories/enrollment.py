from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.cohort import Cohort
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.program import Program


def get_active_enrollment_for_user(db: Session, user_id: UUID) -> Enrollment | None:
    """
    Fetch the active enrollment for a user, eagerly loading Cohort and Program.
    """
    stmt = (
        select(Enrollment)
        .options(
            joinedload(Enrollment.cohort).joinedload(Cohort.program),
        )
        .where(
            Enrollment.user_id == user_id,
            Enrollment.enrollment_status == EnrollmentStatus.ACTIVE,
        )
        .order_by(Enrollment.created_at.desc())
    )
    return db.scalars(stmt).first()


def get_enrollment_by_user_and_cohort(
    db: Session, user_id: UUID, cohort_id: UUID
) -> Enrollment | None:
    stmt = select(Enrollment).where(
        Enrollment.user_id == user_id,
        Enrollment.cohort_id == cohort_id,
    )
    return db.scalars(stmt).first()
