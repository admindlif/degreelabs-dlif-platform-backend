from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import require_fellow_portal
from app.db.session import get_db
from app.models.user import User
from app.schemas.discover import (
    DiscoverOverviewResponse,
    DiscoverWeekResponse,
    SessionDetailResponse,
    SessionSummary,
)
from app.schemas.fellow_context import FellowContextResponse
from app.schemas.team_resource import (
    CompanyChallengeResponse,
    ResourceResponse,
    TeamResponse,
)
from app.services.discover import (
    get_discover_overview,
    get_discover_weeks,
    get_fellow_sessions,
    get_session_detail,
)
from app.services.fellow_context import get_fellow_context
from app.services.resource import (
    get_fellow_resources,
    get_fellow_session_resources,
)
from app.services.team import (
    get_fellow_company_challenge,
    get_fellow_team,
)
from app.schemas.submission import (
    SessionSubmissionResponse,
    TeamSubmissionUpsertRequest,
)
from app.services.submission import (
    get_fellow_session_submission,
    upsert_fellow_session_submission,
)

from app.schemas.feedback import SubmissionFeedbackDetail
from app.services.feedback import get_fellow_session_feedback
from app.schemas.checklist import (
    ChecklistCompletionUpdate,
    FellowChecklistItemResponse,
    FellowChecklistResponse,
)
from app.services.checklist import (
    get_fellow_checklist,
    update_fellow_checklist_completion,
)
router = APIRouter(prefix="/fellow", tags=["Fellow Portal"])


@router.get(
    "/context",
    response_model=FellowContextResponse,
    summary="Get Fellow context (program, cohort, active phase)",
)
def get_context(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowContextResponse:
    return get_fellow_context(db, current_user)


@router.get(
    "/discover/overview",
    response_model=DiscoverOverviewResponse,
    summary="Get DISCOVER phase overview for authenticated Fellow",
)
def get_overview(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> DiscoverOverviewResponse:
    return get_discover_overview(db, current_user)


@router.get(
    "/discover/weeks",
    response_model=list[DiscoverWeekResponse],
    summary="Get all DISCOVER weeks and sessions for authenticated Fellow",
)
def get_weeks(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> list[DiscoverWeekResponse]:
    return get_discover_weeks(db, current_user)


@router.get(
    "/discover/weeks/{week_id}",
    response_model=DiscoverWeekResponse,
    summary="Get specific DISCOVER week details and sessions",
)
def get_week_by_id(
    week_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> DiscoverWeekResponse:
    weeks = get_discover_weeks(db, current_user)
    for w in weeks:
        if w.id == week_id:
            return w
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Week not found in active phase.",
    )



@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="Get all Sessions for authenticated Fellow's active Cohort",
)
def get_sessions(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> list[SessionSummary]:
    return get_fellow_sessions(
        db,
        current_user,
    )

@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    summary="Get session details by ID for authenticated Fellow",
)
def get_session(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> SessionDetailResponse:
    return get_session_detail(db, current_user, session_id)

@router.get(
    "/sessions/{session_id}/resources",
    response_model=list[ResourceResponse],
    summary="Get resources for an unlocked Session",
)
def get_session_resources(
    session_id: UUID,
    current_user: User = Depends(
        require_fellow_portal
    ),
    db: Session = Depends(get_db),
) -> list[ResourceResponse]:
    return get_fellow_session_resources(
        db,
        current_user,
        session_id,
    )

@router.get(
    "/sessions/{session_id}/submission",
    response_model=SessionSubmissionResponse,
    summary="Get Team submission for an unlocked Session",
)
def get_session_submission(
    session_id: UUID,
    current_user: User = Depends(
        require_fellow_portal
    ),
    db: Session = Depends(get_db),
) -> SessionSubmissionResponse:
    return get_fellow_session_submission(
        db,
        current_user,
        session_id,
    )


@router.put(
    "/sessions/{session_id}/submission",
    response_model=SessionSubmissionResponse,
    summary="Submit or resubmit Team work",
)
def upsert_session_submission(
    session_id: UUID,
    data: TeamSubmissionUpsertRequest,
    current_user: User = Depends(
        require_fellow_portal
    ),
    db: Session = Depends(get_db),
) -> SessionSubmissionResponse:
    return upsert_fellow_session_submission(
        db,
        current_user,
        session_id,
        data,
    )


@router.get(
    "/sessions/{session_id}/feedback",
    response_model=SubmissionFeedbackDetail | None,
    summary="Get Team feedback for a Session",
)
def get_session_feedback(
    session_id: UUID,
    current_user: User = Depends(
        require_fellow_portal
    ),
    db: Session = Depends(get_db),
):
    return get_fellow_session_feedback(
        db=db,
        current_user=current_user,
        session_id=session_id,
    )


@router.get(
    "/company-challenge",
    response_model=CompanyChallengeResponse,
    summary="Get the authenticated Fellow's Company Challenge",
)
def get_company_challenge(
    current_user: User = Depends(
        require_fellow_portal
    ),
    db: Session = Depends(get_db),
) -> CompanyChallengeResponse:
    return get_fellow_company_challenge(
        db,
        current_user,
    )
@router.get(
    "/team",
    response_model=TeamResponse,
    summary="Get the authenticated Fellow's team and members",
)
def get_team(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> TeamResponse:
    return get_fellow_team(db, current_user)


@router.get(
    "/resources",
    response_model=list[ResourceResponse],
    summary="Get curriculum resources for the Fellow's current phase",
)
def get_resources(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> list[ResourceResponse]:
    return get_fellow_resources(db, current_user)


@router.get(
    "/checklist",
    response_model=FellowChecklistResponse,
    summary="Get the authenticated Fellow's dynamic checklist",
)
def get_checklist(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowChecklistResponse:
    return get_fellow_checklist(db, current_user)


@router.put(
    "/checklist/{item_id}/completion",
    response_model=FellowChecklistItemResponse,
    summary="Update the authenticated Fellow's checklist completion",
)
def update_checklist_completion(
    item_id: UUID,
    data: ChecklistCompletionUpdate,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowChecklistItemResponse:
    return update_fellow_checklist_completion(
        db,
        current_user,
        item_id,
        data.is_completed,
    )
