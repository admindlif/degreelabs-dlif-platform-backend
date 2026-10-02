"""
Admin routes — full CRUD for all platform entities.

Only ADMIN and SUPER_ADMIN roles may access these endpoints.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from uuid import UUID

from app.core.permissions import require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.resource import Resource, ResourceType
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.team import (
    Team,
    TeamMembership,
    TeamMemberRole,
    TeamChallengeResource,
)
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week
from app.schemas.admin_crud import (
    CohortCreate,
    CohortUpdate,
    FellowUpdate,
    PhaseCreate,
    PhaseUpdate,
    ProgramCreate,
    ProgramUpdate,
    ResourceCreate,
    ResourceUpdate,
    SessionCreate,
    SessionUpdate,
    SessionAccessStateResponse,
    TeamCreate,
    TeamUpdate,
    TeamMemberAdd,
    TeamLeadAssign,
    TeamChallengeResourceCreate,
    TeamChallengeResourceUpdate,
    TeamChallengeResourceResponse,
    WeekCreate,
    WeekUpdate,
    TeamChallengeUpdate,
)

from app.services.team import (
    add_member_to_team,
    remove_member_from_team,
    set_team_lead,
)
from app.schemas.auth import (
    CreateFellowRequest,
    CreateFellowResponse,
    CreateStudentResponse,
    InvitationDeliveryResponse,
)
from app.services.auth import DuplicateEmailError, admin_create_fellow
from app.services.invitation import replace_invitation_for_user, send_invitation_email
from app.services.google_calendar import (
    create_calendar_event_with_meet,
    update_calendar_event,
    delete_calendar_event,
)
from app.services.discover_initialization import (
    DiscoverSessionInitializationError,
    initialize_discover_sessions_for_cohort,
)

from app.models.submission import TeamSubmission

from app.schemas.submission import (
    AdminSessionSubmissionItem,
    TeamSubmissionDetail,
)

from app.models.feedback import SubmissionFeedback
from app.schemas.feedback import (
    SubmissionFeedbackDetail,
    SubmissionFeedbackUpsertRequest,
)
from app.schemas.checklist import (
    AdminChecklistItemResponse,
    ChecklistItemCreate,
    ChecklistItemUpdate,
)
from app.services.checklist import (
    create_admin_checklist_item,
    delete_admin_checklist_item,
    list_admin_checklist_items,
    update_admin_checklist_item,
)
router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


def _not_found(entity: str, id: object) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} with id={id} not found.")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# ===========================================================================
# DASHBOARD STATS
# ===========================================================================

@router.get("/stats", summary="Get platform overview stats")
def get_admin_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
):
    total_fellows = db.scalar(select(func.count(User.id)).where(User.role.in_([UserRole.FELLOW, UserRole.STUDENT]))) or 0
    active_fellows = db.scalar(select(func.count(User.id)).where(User.role.in_([UserRole.FELLOW, UserRole.STUDENT]), User.account_status == AccountStatus.ACTIVE)) or 0
    invited_fellows = db.scalar(select(func.count(User.id)).where(User.role.in_([UserRole.FELLOW, UserRole.STUDENT]), User.account_status == AccountStatus.INVITED)) or 0
    total_cohorts = db.scalar(select(func.count(Cohort.id))) or 0
    total_teams = db.scalar(select(func.count(Team.id))) or 0
    total_sessions = db.scalar(select(func.count(DBSession.id))) or 0
    return {"total_fellows": total_fellows, "active_fellows": active_fellows, "invited_fellows": invited_fellows, "total_cohorts": total_cohorts, "total_teams": total_teams, "total_sessions": total_sessions, "current_phase": "DISCOVER (THINK)", "week": "Week 1 of 4"}


# ===========================================================================
# CHECKLIST ITEMS CRUD
# ===========================================================================

@router.get(
    "/checklist-items",
    response_model=list[AdminChecklistItemResponse],
    summary="List checklist item definitions",
)
def list_checklist_items(
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
    ),
):
    return list_admin_checklist_items(db)


@router.post(
    "/checklist-items",
    response_model=AdminChecklistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a checklist item definition",
)
def create_checklist_item(
    data: ChecklistItemCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
    ),
):
    return create_admin_checklist_item(db, data)


@router.put(
    "/checklist-items/{item_id}",
    response_model=AdminChecklistItemResponse,
    summary="Update a checklist item definition",
)
def update_checklist_item(
    item_id: UUID,
    data: ChecklistItemUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
    ),
):
    return update_admin_checklist_item(db, item_id, data)


@router.delete(
    "/checklist-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete or deactivate a checklist item definition",
)
def delete_checklist_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
    ),
):
    delete_admin_checklist_item(db, item_id)


# ===========================================================================
# FELLOWS CRUD
# ===========================================================================

@router.post("/fellows", response_model=CreateFellowResponse, status_code=status.HTTP_201_CREATED, summary="Create a Fellow and send invite")
@router.post("/students", response_model=CreateFellowResponse, status_code=status.HTTP_201_CREATED, deprecated=True, summary="Legacy alias")
def create_fellow(data: CreateFellowRequest, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))) -> CreateFellowResponse:
    try:
        user, raw_token = admin_create_fellow(db, data)
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    invitation_sent = True
    message = "Fellow created and invitation email sent."
    try:
        send_invitation_email(user=user, raw_token=raw_token)
    except Exception:
        logger.exception("Failed to send invitation email to user_id=%s", user.id)
        invitation_sent = False
        message = (
            "Fellow created, but the invitation email could not be sent. "
            "Retry the invitation delivery without creating another account."
        )
    response = CreateStudentResponse.model_validate(user)
    return response.model_copy(
        update={
            "invitation_sent": invitation_sent,
            "message": message,
        }
    )


@router.post(
    "/fellows/{fellow_id}/resend-invitation",
    response_model=InvitationDeliveryResponse,
    summary="Replace and resend a Fellow invitation",
)
def resend_fellow_invitation(
    fellow_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
) -> InvitationDeliveryResponse:
    user = db.scalar(
        select(User).where(
            User.id == fellow_id,
            User.role.in_([UserRole.FELLOW, UserRole.STUDENT]),
        )
    )
    if user is None:
        raise _not_found("Fellow", fellow_id)
    if (
        user.account_status != AccountStatus.INVITED
        or user.password_set_at is not None
    ):
        raise _conflict(
            "Invitation cannot be resent after onboarding has started or completed."
        )

    raw_token = replace_invitation_for_user(db, user)
    db.commit()

    try:
        send_invitation_email(user=user, raw_token=raw_token)
    except Exception:
        logger.exception(
            "Failed to resend invitation email to user_id=%s", user.id
        )
        return InvitationDeliveryResponse(
            invitation_sent=False,
            message=(
                "A replacement invitation was created, but email delivery failed. "
                "Review the mail service and retry."
            ),
        )

    return InvitationDeliveryResponse(
        invitation_sent=True,
        message="Replacement invitation email sent.",
    )


@router.get("/fellows", summary="List all Fellows")
def list_fellows(db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    users = db.scalars(select(User).where(User.role.in_([UserRole.FELLOW, UserRole.STUDENT])).order_by(User.created_at.desc())).all()
    return [{"id": str(u.id), "first_name": u.first_name, "last_name": u.last_name, "email": u.email, "role": u.role.value, "account_status": u.account_status.value, "two_factor_enabled": u.two_factor_enabled, "created_at": u.created_at.isoformat() if u.created_at else None, "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None} for u in users]


@router.get("/fellows/{fellow_id}", summary="Get a Fellow by ID")
def get_fellow(fellow_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    user = db.scalar(select(User).where(User.id == fellow_id))
    if not user:
        raise _not_found("Fellow", fellow_id)
    return {"id": str(user.id), "first_name": user.first_name, "last_name": user.last_name, "email": user.email, "role": user.role.value, "account_status": user.account_status.value, "two_factor_enabled": user.two_factor_enabled, "created_at": user.created_at.isoformat() if user.created_at else None, "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None}


@router.put("/fellows/{fellow_id}", summary="Update a Fellow")
def update_fellow(fellow_id: str, data: FellowUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    user = db.scalar(select(User).where(User.id == fellow_id))
    if not user:
        raise _not_found("Fellow", fellow_id)
    updates = data.model_dump(exclude_none=True)
    if "account_status" in updates:
        updates["account_status"] = AccountStatus(updates["account_status"])
    if "role" in updates:
        updates["role"] = UserRole(updates["role"])
    for field, value in updates.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "first_name": user.first_name, "last_name": user.last_name, "email": user.email, "role": user.role.value, "account_status": user.account_status.value}


@router.delete("/fellows/{fellow_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Fellow")
def delete_fellow(fellow_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    user = db.scalar(select(User).where(User.id == fellow_id))
    if not user:
        raise _not_found("Fellow", fellow_id)
    db.delete(user)
    db.commit()


# ===========================================================================
# PROGRAMS CRUD
# ===========================================================================

@router.get("/programs", summary="List all Programs")
def list_programs(db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    programs = db.scalars(select(Program).order_by(Program.created_at.desc())).all()
    return [{"id": str(p.id), "name": p.name, "code": p.code, "description": p.description, "is_active": p.is_active, "created_at": p.created_at.isoformat() if p.created_at else None} for p in programs]


@router.post("/programs", status_code=status.HTTP_201_CREATED, summary="Create a Program")
def create_program(data: ProgramCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    if db.scalar(select(Program).where(Program.code == data.code)):
        raise _conflict(f"Program with code '{data.code}' already exists.")
    program = Program(**data.model_dump())
    db.add(program)
    db.commit()
    db.refresh(program)
    return {"id": str(program.id), "name": program.name, "code": program.code}


@router.get("/programs/{program_id}", summary="Get a Program by ID")
def get_program(program_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    program = db.scalar(select(Program).where(Program.id == program_id))
    if not program:
        raise _not_found("Program", program_id)
    return {"id": str(program.id), "name": program.name, "code": program.code, "description": program.description, "is_active": program.is_active}


@router.put("/programs/{program_id}", summary="Update a Program")
def update_program(program_id: str, data: ProgramUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    program = db.scalar(select(Program).where(Program.id == program_id))
    if not program:
        raise _not_found("Program", program_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(program, field, value)
    db.commit()
    db.refresh(program)
    return {"id": str(program.id), "name": program.name, "code": program.code, "is_active": program.is_active}


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Program")
def delete_program(program_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    program = db.scalar(select(Program).where(Program.id == program_id))
    if not program:
        raise _not_found("Program", program_id)
    db.delete(program)
    db.commit()


# ===========================================================================
# PHASES CRUD
# ===========================================================================

@router.get("/phases", summary="List Phases (filter by program_id)")
def list_phases(program_id: str | None = None, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    q = select(Phase).order_by(Phase.sequence)
    if program_id:
        q = q.where(Phase.program_id == program_id)
    phases = db.scalars(q).all()
    return [{"id": str(p.id), "program_id": str(p.program_id), "code": p.code, "name": p.name, "development_role": p.development_role, "sequence": p.sequence, "description": p.description, "duration_weeks": p.duration_weeks, "is_active": p.is_active} for p in phases]


@router.post("/phases", status_code=status.HTTP_201_CREATED, summary="Create a Phase")
def create_phase(data: PhaseCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    if not db.scalar(select(Program).where(Program.id == data.program_id)):
        raise _not_found("Program", data.program_id)
    phase = Phase(**data.model_dump())
    db.add(phase)
    db.commit()
    db.refresh(phase)
    return {"id": str(phase.id), "name": phase.name, "code": phase.code}


@router.get("/phases/{phase_id}", summary="Get a Phase by ID")
def get_phase(phase_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    phase = db.scalar(select(Phase).where(Phase.id == phase_id))
    if not phase:
        raise _not_found("Phase", phase_id)
    return {"id": str(phase.id), "program_id": str(phase.program_id), "code": phase.code, "name": phase.name, "development_role": phase.development_role, "sequence": phase.sequence, "description": phase.description, "duration_weeks": phase.duration_weeks, "is_active": phase.is_active}


@router.put("/phases/{phase_id}", summary="Update a Phase")
def update_phase(phase_id: str, data: PhaseUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    phase = db.scalar(select(Phase).where(Phase.id == phase_id))
    if not phase:
        raise _not_found("Phase", phase_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(phase, field, value)
    db.commit()
    db.refresh(phase)
    return {"id": str(phase.id), "name": phase.name, "is_active": phase.is_active}


@router.delete("/phases/{phase_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Phase")
def delete_phase(phase_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    phase = db.scalar(select(Phase).where(Phase.id == phase_id))
    if not phase:
        raise _not_found("Phase", phase_id)
    db.delete(phase)
    db.commit()


# ===========================================================================
# COHORTS CRUD
# ===========================================================================

@router.get("/cohorts", summary="List all Cohorts")
def list_cohorts(db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    cohorts = db.scalars(select(Cohort).order_by(Cohort.created_at.desc())).all()
    res = []
    for c in cohorts:
        count = db.scalar(select(func.count(Enrollment.id)).where(Enrollment.cohort_id == c.id)) or 0
        res.append({"id": str(c.id), "program_id": str(c.program_id), "name": c.name, "code": c.code, "start_date": c.start_date.isoformat() if c.start_date else None, "end_date": c.end_date.isoformat() if c.end_date else None, "status": c.status.value if hasattr(c.status, "value") else str(c.status), "participant_count": count})
    return res


@router.post("/cohorts", status_code=status.HTTP_201_CREATED, summary="Create a Cohort")
def create_cohort(data: CohortCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    if db.scalar(select(Cohort).where(Cohort.code == data.code)):
        raise _conflict(f"Cohort with code '{data.code}' already exists.")
    payload = data.model_dump()
    payload["status"] = CohortStatus(payload["status"])
    cohort = Cohort(**payload)
    try:
        db.add(cohort)
        db.flush()
        initialize_discover_sessions_for_cohort(db, cohort)
        db.commit()
    except DiscoverSessionInitializationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    db.refresh(cohort)
    return {"id": str(cohort.id), "name": cohort.name, "code": cohort.code}


@router.get("/cohorts/{cohort_id}", summary="Get a Cohort by ID")
def get_cohort(cohort_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    cohort = db.scalar(select(Cohort).where(Cohort.id == cohort_id))
    if not cohort:
        raise _not_found("Cohort", cohort_id)
    count = db.scalar(select(func.count(Enrollment.id)).where(Enrollment.cohort_id == cohort.id)) or 0
    return {"id": str(cohort.id), "program_id": str(cohort.program_id), "name": cohort.name, "code": cohort.code, "start_date": cohort.start_date.isoformat() if cohort.start_date else None, "end_date": cohort.end_date.isoformat() if cohort.end_date else None, "status": cohort.status.value if hasattr(cohort.status, "value") else str(cohort.status), "participant_count": count}


@router.put("/cohorts/{cohort_id}", summary="Update a Cohort")
def update_cohort(cohort_id: str, data: CohortUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    cohort = db.scalar(select(Cohort).where(Cohort.id == cohort_id))
    if not cohort:
        raise _not_found("Cohort", cohort_id)
    updates = data.model_dump(exclude_none=True)
    if "status" in updates:
        updates["status"] = CohortStatus(updates["status"])
    for field, value in updates.items():
        setattr(cohort, field, value)
    db.commit()
    db.refresh(cohort)
    return {"id": str(cohort.id), "name": cohort.name, "code": cohort.code}


@router.delete("/cohorts/{cohort_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Cohort")
def delete_cohort(cohort_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    cohort = db.scalar(select(Cohort).where(Cohort.id == cohort_id))
    if not cohort:
        raise _not_found("Cohort", cohort_id)
    db.delete(cohort)
    db.commit()

# ===========================================================================
# COHORT FELLOWS / ENROLLMENTS
# ===========================================================================


@router.get(
    "/cohorts/{cohort_id}/fellows",
    summary="List Fellows enrolled in a Cohort",
)
def list_cohort_fellows(
    cohort_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    cohort = db.scalar(
        select(Cohort).where(Cohort.id == cohort_id)
    )

    if not cohort:
        raise _not_found("Cohort", cohort_id)

    rows = db.execute(
        select(Enrollment, User)
        .join(
            User,
            User.id == Enrollment.user_id,
        )
        .where(
            Enrollment.cohort_id == cohort_id
        )
        .order_by(
            User.first_name,
            User.last_name,
        )
    ).all()

    return [
        {
            "enrollment_id": str(enrollment.id),
            "fellow_id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "account_status": user.account_status.value,
            "enrollment_status": enrollment.enrollment_status.value,
        }
        for enrollment, user in rows
    ]


@router.post(
    "/cohorts/{cohort_id}/fellows/{fellow_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Enroll Fellow into Cohort",
)
def enroll_fellow_in_cohort(
    cohort_id: UUID,
    fellow_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    cohort = db.scalar(
        select(Cohort).where(Cohort.id == cohort_id)
    )

    if not cohort:
        raise _not_found("Cohort", cohort_id)

    fellow = db.scalar(
        select(User).where(
            User.id == fellow_id,
            User.role.in_(
                [
                    UserRole.FELLOW,
                    UserRole.STUDENT,
                ]
            ),
        )
    )

    if not fellow:
        raise _not_found("Fellow", fellow_id)

    # Prevent one Fellow from having two active cohorts.
    other_active_enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == fellow_id,
            Enrollment.cohort_id != cohort_id,
            Enrollment.enrollment_status
            == EnrollmentStatus.ACTIVE,
        )
    )

    if other_active_enrollment:
        other_cohort = db.scalar(
            select(Cohort).where(
                Cohort.id
                == other_active_enrollment.cohort_id
            )
        )

        raise _conflict(
            f"Fellow is already actively enrolled in "
            f"'{other_cohort.name if other_cohort else 'another cohort'}'."
        )

    existing = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == fellow_id,
            Enrollment.cohort_id == cohort_id,
        )
    )

    if existing:
        existing.enrollment_status = (
            EnrollmentStatus.ACTIVE
        )
        existing.completed_at = None

        enrollment = existing

    else:
        enrollment = Enrollment(
            user_id=fellow_id,
            cohort_id=cohort_id,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )

        db.add(enrollment)

    db.commit()
    db.refresh(enrollment)

    return {
        "id": str(enrollment.id),
        "fellow_id": str(fellow.id),
        "cohort_id": str(cohort.id),
        "enrollment_status": enrollment.enrollment_status.value,
    }


@router.delete(
    "/cohorts/{cohort_id}/fellows/{fellow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Fellow from Cohort",
)
def remove_fellow_from_cohort(
    cohort_id: UUID,
    fellow_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.cohort_id == cohort_id,
            Enrollment.user_id == fellow_id,
        )
    )

    if not enrollment:
        raise _not_found(
            "Enrollment",
            f"{cohort_id}/{fellow_id}",
        )

    # Fellow must be removed from their Team first.
    team_membership = db.scalar(
        select(TeamMembership).where(
            TeamMembership.cohort_id == cohort_id,
            TeamMembership.user_id == fellow_id,
        )
    )

    if team_membership:
        raise _conflict(
            "Remove the Fellow from their Team before "
            "removing them from the Cohort."
        )

    db.delete(enrollment)
    db.commit()

# ===========================================================================
# WEEKS CRUD
# ===========================================================================

@router.get("/weeks", summary="List Weeks (filter by phase_id)")
def list_weeks(phase_id: str | None = None, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    q = select(Week).order_by(Week.sequence)
    if phase_id:
        q = q.where(Week.phase_id == phase_id)
    weeks = db.scalars(q).all()
    return [{"id": str(w.id), "phase_id": str(w.phase_id), "week_number": w.week_number, "title": w.title, "strategic_question": w.strategic_question, "description": w.description, "sequence": w.sequence, "unlock_at": w.unlock_at.isoformat() if w.unlock_at else None} for w in weeks]


@router.post("/weeks", status_code=status.HTTP_201_CREATED, summary="Create a Week")
def create_week(data: WeekCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    if not db.scalar(select(Phase).where(Phase.id == data.phase_id)):
        raise _not_found("Phase", data.phase_id)
    week = Week(**data.model_dump())
    db.add(week)
    db.commit()
    db.refresh(week)
    return {"id": str(week.id), "title": week.title, "week_number": week.week_number}


@router.get("/weeks/{week_id}", summary="Get a Week by ID")
def get_week(week_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    week = db.scalar(select(Week).where(Week.id == week_id))
    if not week:
        raise _not_found("Week", week_id)
    return {"id": str(week.id), "phase_id": str(week.phase_id), "week_number": week.week_number, "title": week.title, "strategic_question": week.strategic_question, "description": week.description, "sequence": week.sequence, "unlock_at": week.unlock_at.isoformat() if week.unlock_at else None}


@router.put("/weeks/{week_id}", summary="Update a Week")
def update_week(week_id: str, data: WeekUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    week = db.scalar(select(Week).where(Week.id == week_id))
    if not week:
        raise _not_found("Week", week_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(week, field, value)
    db.commit()
    db.refresh(week)
    return {"id": str(week.id), "title": week.title, "week_number": week.week_number}


@router.delete("/weeks/{week_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Week")
def delete_week(week_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    week = db.scalar(select(Week).where(Week.id == week_id))
    if not week:
        raise _not_found("Week", week_id)
    db.delete(week)
    db.commit()


# ===========================================================================
# SESSIONS CRUD
# ===========================================================================

@router.get(
    "/sessions",
    summary="List Sessions (filter by cohort_id or week_id)",
)
def list_sessions(
    cohort_id: str | None = None,
    week_id: str | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    q = select(DBSession)

    if cohort_id:
        q = q.where(
            DBSession.cohort_id == cohort_id
        )

    if week_id:
        q = q.where(
            DBSession.week_id == week_id
        )

    if cohort_id:
        q = q.order_by(
            DBSession.sequence.asc(),
            DBSession.session_number.asc(),
        )
    else:
        q = q.order_by(
            DBSession.cohort_id.asc(),
            DBSession.sequence.asc(),
            DBSession.session_number.asc(),
        )

    sessions = db.scalars(q).all()

    return [
        {
            "id": str(session.id),
            "cohort_id": str(session.cohort_id),
            "phase_id": str(session.phase_id),
            "week_id": (
                str(session.week_id)
                if session.week_id
                else None
            ),

            "session_number": session.session_number,

            "session_type": (
                session.session_type.value
                if hasattr(
                    session.session_type,
                    "value",
                )
                else str(session.session_type)
            ),

            "title": session.title,
            "description": session.description,

            "start_at": (
                session.start_at.isoformat()
                if session.start_at
                else None
            ),

            "end_at": (
                session.end_at.isoformat()
                if session.end_at
                else None
            ),

            # ---------------------------------
            # Admin-controlled Session access
            # ---------------------------------
            "is_unlocked": session.is_unlocked,

            "unlock_at": (
                session.unlock_at.isoformat()
                if session.unlock_at
                else None
            ),

            "submission_enabled": (
                session.submission_enabled
            ),

            # ---------------------------------
            # Meeting
            # ---------------------------------
            "meeting_url": session.meeting_url,

            "meeting_provider": (
                session.meeting_provider
            ),

            "google_meet_code": (
                session.google_meet_code
            ),

            "google_calendar_event_id": (
                session.google_calendar_event_id
            ),

            "google_calendar_event_url": (
                session.google_calendar_event_url
            ),

            # ---------------------------------
            # Recording / Transcript
            # ---------------------------------
            "recording_url": (
                session.recording_url
            ),

            "transcript_url": (
                session.transcript_url
            ),

            "status": (
                session.status.value
                if hasattr(session.status, "value")
                else str(session.status)
            ),

            "sequence": session.sequence,
        }
        for session in sessions
    ]

@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="Create Session with Google Calendar and Meet",
)
def create_session(
    data: SessionCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    # ---------------------------------------------------------
    # 1. Validate schedule
    # ---------------------------------------------------------
    if not data.start_at or not data.end_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Start date/time and end date/time are required.",
        )

    if data.end_at <= data.start_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="End date/time must be after start date/time.",
        )

    # ---------------------------------------------------------
    # 2. Validate Cohort
    # ---------------------------------------------------------
    cohort = db.scalar(
        select(Cohort).where(
            Cohort.id == data.cohort_id
        )
    )

    if not cohort:
        raise _not_found(
            "Cohort",
            data.cohort_id,
        )

    # ---------------------------------------------------------
    # 3. Validate Phase
    # ---------------------------------------------------------
    phase = db.scalar(
        select(Phase).where(
            Phase.id == data.phase_id,
            Phase.program_id == cohort.program_id,
        )
    )

    if not phase:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Selected Phase does not belong "
                "to the Cohort's Program."
            ),
        )

    # ---------------------------------------------------------
    # 4. Validate Week when provided
    # ---------------------------------------------------------
    if data.week_id:
        week = db.scalar(
            select(Week).where(
                Week.id == data.week_id,
                Week.phase_id == data.phase_id,
            )
        )

        if not week:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Selected Week does not belong "
                    "to the selected Phase."
                ),
            )

    # ---------------------------------------------------------
    # 5. Google Calendar must be enabled
    # ---------------------------------------------------------
    if not settings.google_calendar_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Calendar integration is disabled.",
        )

    # ---------------------------------------------------------
    # 6. Prevent duplicate Session number in Cohort
    # ---------------------------------------------------------
    existing_session = db.scalar(
        select(DBSession).where(
            DBSession.cohort_id == data.cohort_id,
            DBSession.session_number
            == data.session_number,
        )
    )

    if existing_session:
        raise _conflict(
            f"Session {data.session_number} already exists "
            f"for cohort '{cohort.name}'."
        )

    # ---------------------------------------------------------
    # 7. Collect Fellows enrolled in this Cohort
    # ---------------------------------------------------------
    attendee_emails = list(
        db.scalars(
            select(User.email)
            .join(
                Enrollment,
                Enrollment.user_id == User.id,
            )
            .where(
                Enrollment.cohort_id
                == data.cohort_id,
                Enrollment.enrollment_status
                == EnrollmentStatus.ACTIVE,
                User.role.in_(
                    [
                        UserRole.FELLOW,
                        UserRole.STUDENT,
                    ]
                ),
                User.is_active.is_(True),
            )
            .order_by(User.email)
        ).all()
    )

    # ---------------------------------------------------------
    # 8. Build Session payload
    # ---------------------------------------------------------
    payload = data.model_dump()

    payload["session_type"] = SessionType(
        payload["session_type"]
    )

    payload["status"] = SessionStatus(
        payload["status"]
    )

    # Meet URL is always generated by Google Calendar.
    payload["meeting_url"] = None

    # Manual access rule:
    # Sessions 0-2 initially unlocked.
    # Session 3+ locked until Admin explicitly unlocks.
    if data.session_number <= 2:
        payload["is_unlocked"] = True
        payload["unlock_at"] = datetime.now(
            timezone.utc
        )
    else:
        payload["is_unlocked"] = False
        payload["unlock_at"] = None

    session = DBSession(**payload)

    db.add(session)

    try:
        # Ensure DB constraints are checked before
        # creating an external Calendar event.
        db.flush()

        # -----------------------------------------------------
        # 9. Create Calendar event + Google Meet
        #    and email Cohort Fellows.
        # -----------------------------------------------------
        calendar_event = (
            create_calendar_event_with_meet(
                title=session.title,
                description=session.description,
                start_at=session.start_at,
                end_at=session.end_at,
                attendee_emails=attendee_emails,
            )
        )

        # -----------------------------------------------------
        # 10. Save Google metadata
        # -----------------------------------------------------
        session.meeting_provider = (
            "google_calendar"
        )

        session.meeting_url = (
            calendar_event["meeting_url"]
        )

        session.google_meet_code = (
            calendar_event["meeting_code"]
        )

        session.google_calendar_event_id = (
            calendar_event["event_id"]
        )

        session.google_calendar_event_url = (
            calendar_event["calendar_url"]
        )

        db.commit()
        db.refresh(session)

    except Exception as exc:
        db.rollback()
        logger.exception(
            "Google Calendar event creation failed for session_id=%s",
            session.id,
        )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to create the Google Calendar event and Meet link.",
        ) from exc

    return {
        "id": str(session.id),

        "cohort_id": str(session.cohort_id),

        "title": session.title,
        "session_number": session.session_number,

        "start_at": (
            session.start_at.isoformat()
            if session.start_at
            else None
        ),

        "end_at": (
            session.end_at.isoformat()
            if session.end_at
            else None
        ),

        "is_unlocked": session.is_unlocked,

        "meeting_provider": (
            session.meeting_provider
        ),

        "meeting_url": (
            session.meeting_url
        ),

        "google_meet_code": (
            session.google_meet_code
        ),

        "google_calendar_event_id": (
            session.google_calendar_event_id
        ),

        "google_calendar_event_url": (
            session.google_calendar_event_url
        ),

        "attendee_count": len(
            attendee_emails
        ),

        "status": session.status.value,
    }
@router.get(
    "/sessions/{session_id}",
    summary="Get a Session by ID",
)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    return {
        "id": str(session.id),
        "cohort_id": str(session.cohort_id),
        "phase_id": str(session.phase_id),

        "week_id": (
            str(session.week_id)
            if session.week_id
            else None
        ),

        "session_number": session.session_number,

        "session_type": (
            session.session_type.value
            if hasattr(
                session.session_type,
                "value",
            )
            else str(session.session_type)
        ),

        "title": session.title,
        "description": session.description,

        "start_at": (
            session.start_at.isoformat()
            if session.start_at
            else None
        ),

        "end_at": (
            session.end_at.isoformat()
            if session.end_at
            else None
        ),

        # Admin-controlled Session access
        "is_unlocked": session.is_unlocked,

        "unlock_at": (
            session.unlock_at.isoformat()
            if session.unlock_at
            else None
        ),

        "submission_enabled": (
            session.submission_enabled
        ),

        # Meeting
        "meeting_url": session.meeting_url,
        "meeting_provider": (
            session.meeting_provider
        ),

        "google_meet_space_name": (
            session.google_meet_space_name
        ),

        "google_meet_code": (
            session.google_meet_code
        ),

        "google_calendar_event_id": (
            session.google_calendar_event_id
        ),

        "google_calendar_event_url": (
            session.google_calendar_event_url
        ),

        # Recording / Transcript
        "recording_url": session.recording_url,
        "transcript_url": session.transcript_url,

        "status": (
            session.status.value
            if hasattr(session.status, "value")
            else str(session.status)
        ),

        "sequence": session.sequence,
    }

@router.get(
    "/sessions/{session_id}/submissions",
    response_model=list[AdminSessionSubmissionItem],
    summary="List Team submissions for a Session",
)
def list_session_submissions(
    session_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    # -------------------------------------------------
    # 1. Find Session
    # -------------------------------------------------
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    # -------------------------------------------------
    # 2. Get every Team in this Session's Cohort
    # -------------------------------------------------
    teams = db.scalars(
        select(Team)
        .where(
            Team.cohort_id == session.cohort_id,
            Team.is_active.is_(True),
        )
        .order_by(Team.name)
    ).all()

    results: list[
        AdminSessionSubmissionItem
    ] = []

    for team in teams:

        # ---------------------------------------------
        # Team Lead
        # ---------------------------------------------
        lead_membership = db.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id,
                TeamMembership.team_role
                == TeamMemberRole.LEAD,
            )
        )

        lead_user = None

        if lead_membership:
            lead_user = db.get(
                User,
                lead_membership.user_id,
            )

        # ---------------------------------------------
        # Team Submission
        # ---------------------------------------------
        submission = db.scalar(
            select(TeamSubmission).where(
                TeamSubmission.team_id
                == team.id,

                TeamSubmission.session_id
                == session.id,
            )
        )

        lead_name = None

        if lead_user:
            lead_name = (
                f"{lead_user.first_name or ''} "
                f"{lead_user.last_name or ''}"
            ).strip()

        results.append(
            AdminSessionSubmissionItem(
                team_id=team.id,
                team_name=team.name,

                team_lead_user_id=(
                    lead_user.id
                    if lead_user
                    else None
                ),

                team_lead_name=(
                    lead_name or None
                ),

                submission=(
                    TeamSubmissionDetail.model_validate(
                        submission
                    )
                    if submission
                    else None
                ),
            )
        )

    return results

@router.get(
    "/submissions/{submission_id}/feedback",
    response_model=SubmissionFeedbackDetail | None,
    summary="Get feedback for a Team submission",
)
def get_submission_feedback(
    submission_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    submission = db.scalar(
        select(TeamSubmission).where(
            TeamSubmission.id == submission_id
        )
    )

    if not submission:
        raise _not_found(
            "TeamSubmission",
            submission_id,
        )

    feedback = db.scalar(
        select(SubmissionFeedback).where(
            SubmissionFeedback.submission_id
            == submission_id
        )
    )

    if not feedback:
        return None

    return feedback

@router.put(
    "/submissions/{submission_id}/feedback",
    response_model=SubmissionFeedbackDetail,
    summary="Create or update feedback for a Team submission",
)
def upsert_submission_feedback(
    submission_id: UUID,
    data: SubmissionFeedbackUpsertRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    submission = db.scalar(
        select(TeamSubmission).where(
            TeamSubmission.id == submission_id
        )
    )

    if not submission:
        raise _not_found(
            "TeamSubmission",
            submission_id,
        )

    feedback = db.scalar(
        select(SubmissionFeedback).where(
            SubmissionFeedback.submission_id
            == submission_id
        )
    )

    feedback_text = data.feedback_text.strip()

    if not feedback_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Feedback text cannot be empty.",
        )

    feedback_url = (
        data.feedback_url.strip()
        if data.feedback_url
        else None
    )

    if feedback:
        feedback.feedback_text = feedback_text
        feedback.feedback_url = feedback_url
        feedback.status = data.status
        feedback.reviewed_by_user_id = admin.id

    else:
        feedback = SubmissionFeedback(
            submission_id=submission_id,
            reviewed_by_user_id=admin.id,
            feedback_text=feedback_text,
            feedback_url=feedback_url,
            status=data.status,
        )

        db.add(feedback)

    db.commit()
    db.refresh(feedback)

    return feedback
@router.put(
    "/sessions/{session_id}",
    summary="Update a Session",
)
def update_session(
    session_id: str,
    data: SessionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    updates = data.model_dump(
        exclude_none=True
    )

    if "session_type" in updates:
        updates["session_type"] = SessionType(
            updates["session_type"]
        )

    if "status" in updates:
        updates["status"] = SessionStatus(
            updates["status"]
        )

    # Apply changes in memory first.
    for field, value in updates.items():
        setattr(
            session,
            field,
            value,
        )

    # Validate resulting schedule.
    if not session.start_at or not session.end_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Start date/time and end date/time "
                "are required."
            ),
        )

    if session.end_at <= session.start_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "End date/time must be after "
                "start date/time."
            ),
        )

    # Get currently active Fellows from this Session's Cohort.
    attendee_emails = list(
        db.scalars(
            select(User.email)
            .join(
                Enrollment,
                Enrollment.user_id == User.id,
            )
            .where(
                Enrollment.cohort_id
                == session.cohort_id,
                Enrollment.enrollment_status
                == EnrollmentStatus.ACTIVE,
                User.role.in_(
                    [
                        UserRole.FELLOW,
                        UserRole.STUDENT,
                    ]
                ),
                User.is_active.is_(True),
            )
            .order_by(User.email)
        ).all()
    )

    try:
        if not settings.google_calendar_enabled:
            raise RuntimeError(
                "Google Calendar integration "
                "is disabled."
            )

        # Existing Calendar-backed Session:
        # update the same event instead of creating another.
        if session.google_calendar_event_id:
            calendar_event = update_calendar_event(
                event_id=session.google_calendar_event_id,
                title=session.title,
                description=session.description,
                start_at=session.start_at,
                end_at=session.end_at,
                attendee_emails=attendee_emails,
            )

            session.meeting_url = (
                calendar_event["meeting_url"]
                or session.meeting_url
            )

            session.google_meet_code = (
                calendar_event["meeting_code"]
                or session.google_meet_code
            )

            session.google_calendar_event_url = (
                calendar_event["calendar_url"]
                or session.google_calendar_event_url
            )

        # Canonical/initialized Session:
        # create its first Calendar event + Google Meet.
        else:
            calendar_event = create_calendar_event_with_meet(
                title=session.title,
                description=session.description,
                start_at=session.start_at,
                end_at=session.end_at,
                attendee_emails=attendee_emails,
            )

            session.meeting_provider = "google_calendar"

            session.meeting_url = (
                calendar_event["meeting_url"]
            )

            session.google_meet_code = (
                calendar_event["meeting_code"]
            )

            session.google_calendar_event_id = (
                calendar_event["event_id"]
            )

            session.google_calendar_event_url = (
                calendar_event["calendar_url"]
            )

        db.commit()
        db.refresh(session)

    except Exception as exc:
        db.rollback()

        logger.exception(
            "Google Calendar event update failed for session_id=%s",
            session.id,
        )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Unable to update the Session "
                "and Google Calendar event."
            ),
        ) from exc

    return {
        "id": str(session.id),
        "title": session.title,

        "start_at": (
            session.start_at.isoformat()
            if session.start_at
            else None
        ),

        "end_at": (
            session.end_at.isoformat()
            if session.end_at
            else None
        ),

        "meeting_url": session.meeting_url,

        "google_calendar_event_id": (
            session.google_calendar_event_id
        ),

        "google_calendar_event_url": (
            session.google_calendar_event_url
        ),

        "status": session.status.value,
    }
@router.put(
    "/sessions/{session_id}/unlock",
    response_model=SessionAccessStateResponse,
    summary="Unlock a Session for Fellows",
)
def unlock_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
) -> SessionAccessStateResponse:
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    session.is_unlocked = True
    session.unlock_at = datetime.now(
        timezone.utc
    )

    db.commit()
    db.refresh(session)

    return SessionAccessStateResponse(
        id=session.id,
        session_number=session.session_number,
        is_unlocked=session.is_unlocked,
        unlock_at=session.unlock_at,
    )

@router.put(
    "/sessions/{session_id}/lock",
    response_model=SessionAccessStateResponse,
    summary="Lock a Session for Fellows",
)
def lock_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
) -> SessionAccessStateResponse:
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    session.is_unlocked = False
    session.unlock_at = None

    db.commit()
    db.refresh(session)

    return SessionAccessStateResponse(
        id=session.id,
        session_number=session.session_number,
        is_unlocked=session.is_unlocked,
        unlock_at=session.unlock_at,
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Session",
)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    session = db.scalar(
        select(DBSession).where(
            DBSession.id == session_id
        )
    )

    if not session:
        raise _not_found(
            "Session",
            session_id,
        )

    try:
        # Cancel the Calendar event first.
        # sendUpdates="all" in the service sends
        # cancellation notifications to attendees.
        if session.google_calendar_event_id:
            if not settings.google_calendar_enabled:
                raise RuntimeError(
                    "Google Calendar integration "
                    "is disabled."
                )

            delete_calendar_event(
                session.google_calendar_event_id
            )

        db.delete(session)
        db.commit()

    except Exception as exc:
        db.rollback()
        logger.exception(
            "Google Calendar event deletion failed for session_id=%s",
            session.id,
        )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to delete the Session and Google Calendar event.",
        ) from exc


# ===========================================================================
# TEAMS CRUD
# ===========================================================================

@router.get("/teams", summary="List Teams (filter by cohort_id)")
def list_teams(cohort_id: str | None = None, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    q = select(Team).order_by(Team.created_at.desc())
    if cohort_id:
        q = q.where(Team.cohort_id == cohort_id)
    teams = db.scalars(q).all()
    res = []
    for t in teams:
        members = db.scalars(select(TeamMembership).where(TeamMembership.team_id == t.id)).all()
        res.append({
            "id": str(t.id),
            "cohort_id": str(t.cohort_id),
            "name": t.name,
            "company_challenge": t.company_challenge,
            "company_name": t.company_name,

            "company_overview": t.company_overview,
            "challenge_description": t.challenge_description,

            "is_active": t.is_active,
            "member_count": len(members),
        })
    return res


@router.post("/teams", status_code=status.HTTP_201_CREATED, summary="Create a Team")
def create_team(data: TeamCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    if not db.scalar(select(Cohort).where(Cohort.id == data.cohort_id)):
        raise _not_found("Cohort", data.cohort_id)
    team = Team(**data.model_dump())
    db.add(team)
    db.commit()
    db.refresh(team)
    return {"id": str(team.id), "name": team.name, "cohort_id": str(team.cohort_id)}


@router.get("/teams/{team_id}", summary="Get a Team with members")
def get_team(team_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    team = db.scalar(select(Team).where(Team.id == team_id))
    if not team:
        raise _not_found("Team", team_id)
    memberships = db.scalars(select(TeamMembership).where(TeamMembership.team_id == team.id)).all()
    members = []
    for m in memberships:
        u = db.scalar(select(User).where(User.id == m.user_id))
        members.append({"id": str(m.id), "user_id": str(m.user_id), "team_role": m.team_role.value if hasattr(m.team_role, "value") else str(m.team_role), "joined_at": m.joined_at.isoformat() if m.joined_at else None, "first_name": u.first_name if u else None, "last_name": u.last_name if u else None, "email": u.email if u else None})
    return {
        "id": str(team.id),
        "cohort_id": str(team.cohort_id),
        "name": team.name,
        "company_challenge": team.company_challenge,
        "company_name": team.company_name,
        "company_overview": team.company_overview,
        "challenge_description": team.challenge_description,
        "is_active": team.is_active,
        "member_count": len(members),
        "members": members,
    }


@router.put("/teams/{team_id}", summary="Update a Team")
def update_team(team_id: str, data: TeamUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    team = db.scalar(select(Team).where(Team.id == team_id))
    if not team:
        raise _not_found("Team", team_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(team, field, value)
    db.commit()
    db.refresh(team)
    return {"id": str(team.id), "name": team.name, "is_active": team.is_active}

@router.put(
    "/teams/{team_id}/challenge",
    summary="Update Company and Company Challenge",
)
def update_team_challenge(
    team_id: UUID,
    data: TeamChallengeUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    team = db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )

    if not team:
        raise _not_found(
            "Team",
            team_id,
        )

    updates = data.model_dump(
        exclude_unset=True
    )

    for field, value in updates.items():
        setattr(
            team,
            field,
            value,
        )

    db.commit()
    db.refresh(team)

    return {
        "team_id": str(team.id),
        "company_name": team.company_name,
        "company_overview": team.company_overview,
        "company_challenge": team.company_challenge,
        "challenge_description":
            team.challenge_description,
    }



@router.get(
    "/teams/{team_id}/challenge/resources",
    response_model=list[TeamChallengeResourceResponse],
    summary="List Company Challenge resources",
)
def list_team_challenge_resources(
    team_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    team = db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )

    if not team:
        raise _not_found(
            "Team",
            team_id,
        )

    resources = db.scalars(
        select(TeamChallengeResource)
        .where(
            TeamChallengeResource.team_id
            == team_id
        )
        .order_by(
            TeamChallengeResource.sequence,
            TeamChallengeResource.created_at,
        )
    ).all()

    return resources


@router.post(
    "/teams/{team_id}/challenge/resources",
    response_model=TeamChallengeResourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Company Challenge resource",
)
def create_team_challenge_resource(
    team_id: UUID,
    data: TeamChallengeResourceCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    team = db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )

    if not team:
        raise _not_found(
            "Team",
            team_id,
        )

    resource = TeamChallengeResource(
        team_id=team.id,
        **data.model_dump(),
    )

    db.add(resource)
    db.commit()
    db.refresh(resource)

    return resource

@router.put(
    "/teams/{team_id}/challenge/resources/{resource_id}",
    response_model=TeamChallengeResourceResponse,
    summary="Update Company Challenge resource",
)
def update_team_challenge_resource(
    team_id: UUID,
    resource_id: UUID,
    data: TeamChallengeResourceUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    team = db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )

    if not team:
        raise _not_found(
            "Team",
            team_id,
        )

    resource = db.scalar(
        select(TeamChallengeResource).where(
            TeamChallengeResource.id == resource_id,
            TeamChallengeResource.team_id == team_id,
        )
    )

    if not resource:
        raise _not_found(
            "Team Challenge Resource",
            resource_id,
        )

    updates = data.model_dump(
        exclude_unset=True
    )

    for field, value in updates.items():
        setattr(
            resource,
            field,
            value,
        )

    db.commit()
    db.refresh(resource)

    return resource


@router.delete(
    "/teams/{team_id}/challenge/resources/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Company Challenge resource",
)
def delete_team_challenge_resource(
    team_id: UUID,
    resource_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    team = db.scalar(
        select(Team).where(
            Team.id == team_id
        )
    )

    if not team:
        raise _not_found(
            "Team",
            team_id,
        )

    resource = db.scalar(
        select(TeamChallengeResource).where(
            TeamChallengeResource.id == resource_id,
            TeamChallengeResource.team_id == team_id,
        )
    )

    if not resource:
        raise _not_found(
            "Team Challenge Resource",
            resource_id,
        )

    db.delete(resource)
    db.commit()

@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Team")
def delete_team(team_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))):
    team = db.scalar(select(Team).where(Team.id == team_id))
    if not team:
        raise _not_found("Team", team_id)
    db.delete(team)
    db.commit()


@router.post(
    "/teams/{team_id}/members",
    status_code=status.HTTP_201_CREATED,
    summary="Add a Fellow to a Team",
)
def add_team_member(
    team_id: UUID,
    data: TeamMemberAdd,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    membership = add_member_to_team(
        db=db,
        team_id=team_id,
        user_id=data.user_id,
        team_role=TeamMemberRole(data.team_role),
    )

    return {
        "id": str(membership.id),
        "team_id": str(membership.team_id),
        "user_id": str(membership.user_id),
        "team_role": membership.team_role.value,
    }

@router.delete(
    "/teams/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a Fellow from a Team",
)
def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    remove_member_from_team(
        db=db,
        team_id=team_id,
        user_id=user_id,
    )
@router.put(
    "/teams/{team_id}/lead",
    summary="Assign or change Team Lead",
)
def change_team_lead(
    team_id: UUID,
    data: TeamLeadAssign,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    membership = set_team_lead(
        db=db,
        team_id=team_id,
        user_id=data.user_id,
    )

    return {
        "team_id": str(membership.team_id),
        "user_id": str(membership.user_id),
        "team_role": membership.team_role.value,
    }

# ===========================================================================
# RESOURCES CRUD
# ===========================================================================


@router.get(
    "/resources",
    summary="List Resources",
)
def list_resources(
    phase_id: str | None = None,
    session_id: str | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    q = select(Resource).order_by(
        Resource.sequence
    )

    if phase_id:
        q = q.where(
            Resource.phase_id == phase_id
        )

    if session_id:
        q = q.where(
            Resource.session_id == session_id
        )

    resources = db.scalars(q).all()

    return [
        {
            "id": str(resource.id),
            "phase_id": str(resource.phase_id),

            "session_id": (
                str(resource.session_id)
                if resource.session_id
                else None
            ),

            "title": resource.title,
            "subtitle": resource.subtitle,

            "resource_type": (
                resource.resource_type.value
                if hasattr(
                    resource.resource_type,
                    "value",
                )
                else str(
                    resource.resource_type
                )
            ),

            "url": resource.url,

            "is_downloadable":
                resource.is_downloadable,

            "is_active":
                resource.is_active,

            "sequence":
                resource.sequence,
        }
        for resource in resources
    ]


@router.post(
    "/resources",
    status_code=status.HTTP_201_CREATED,
    summary="Create a Resource",
)
def create_resource(
    data: ResourceCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    phase = db.scalar(
        select(Phase).where(
            Phase.id == data.phase_id
        )
    )

    if not phase:
        raise _not_found(
            "Phase",
            data.phase_id,
        )

    if data.session_id:
        session = db.scalar(
            select(DBSession).where(
                DBSession.id
                == data.session_id
            )
        )

        if not session:
            raise _not_found(
                "Session",
                data.session_id,
            )

        if session.phase_id != data.phase_id:
            raise _conflict(
                "Resource Session must belong "
                "to the selected Phase."
            )

    payload = data.model_dump()

    payload["resource_type"] = (
        ResourceType(
            payload["resource_type"]
        )
    )

    resource = Resource(
        **payload
    )

    db.add(resource)
    db.commit()
    db.refresh(resource)

    return {
        "id": str(resource.id),
        "phase_id": str(resource.phase_id),

        "session_id": (
            str(resource.session_id)
            if resource.session_id
            else None
        ),

        "title": resource.title,

        "resource_type": (
            resource.resource_type.value
        ),

        "url": resource.url,

        "is_active":
            resource.is_active,
    }


@router.get(
    "/resources/{resource_id}",
    summary="Get a Resource by ID",
)
def get_resource(
    resource_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    resource = db.scalar(
        select(Resource).where(
            Resource.id == resource_id
        )
    )

    if not resource:
        raise _not_found(
            "Resource",
            resource_id,
        )

    return {
        "id": str(resource.id),
        "phase_id": str(resource.phase_id),

        "session_id": (
            str(resource.session_id)
            if resource.session_id
            else None
        ),

        "title": resource.title,
        "subtitle": resource.subtitle,

        "resource_type": (
            resource.resource_type.value
            if hasattr(
                resource.resource_type,
                "value",
            )
            else str(
                resource.resource_type
            )
        ),

        "url": resource.url,

        "is_downloadable":
            resource.is_downloadable,

        "is_active":
            resource.is_active,

        "sequence":
            resource.sequence,
    }


@router.put(
    "/resources/{resource_id}",
    summary="Update a Resource",
)
def update_resource(
    resource_id: str,
    data: ResourceUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    resource = db.scalar(
        select(Resource).where(
            Resource.id == resource_id
        )
    )

    if not resource:
        raise _not_found(
            "Resource",
            resource_id,
        )

    # exclude_unset is intentional:
    # session_id=None must be allowed so Admin can
    # move a Session Resource back to phase-level.
    updates = data.model_dump(
        exclude_unset=True
    )

    if "session_id" in updates:
        new_session_id = updates[
            "session_id"
        ]

        if new_session_id is not None:
            session = db.scalar(
                select(DBSession).where(
                    DBSession.id
                    == new_session_id
                )
            )

            if not session:
                raise _not_found(
                    "Session",
                    new_session_id,
                )

            if (
                session.phase_id
                != resource.phase_id
            ):
                raise _conflict(
                    "Resource Session must "
                    "belong to the Resource Phase."
                )

    if "resource_type" in updates:
        updates["resource_type"] = (
            ResourceType(
                updates["resource_type"]
            )
        )

    for field, value in updates.items():
        setattr(
            resource,
            field,
            value,
        )

    db.commit()
    db.refresh(resource)

    return {
        "id": str(resource.id),

        "phase_id":
            str(resource.phase_id),

        "session_id": (
            str(resource.session_id)
            if resource.session_id
            else None
        ),

        "title": resource.title,

        "is_active":
            resource.is_active,
    }


@router.delete(
    "/resources/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Resource",
)
def delete_resource(
    resource_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        )
    ),
):
    resource = db.scalar(
        select(Resource).where(
            Resource.id == resource_id
        )
    )

    if not resource:
        raise _not_found(
            "Resource",
            resource_id,
        )

    db.delete(resource)
    db.commit()
