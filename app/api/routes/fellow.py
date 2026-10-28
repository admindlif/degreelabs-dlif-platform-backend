from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
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
from app.schemas.session_overview import SessionOverviewResponse
from app.services.session_overview import get_fellow_session_overview
from app.services.feedback import get_fellow_session_feedback
from app.schemas.working_board import (
    FellowWorkingBoardInsightsResponse,
    FellowWorkingBoardResponse,
)
from app.services.working_board import (
    get_fellow_working_board,
    get_fellow_working_board_insights,
)
from app.schemas.checklist import (
    ChecklistCompletionUpdate,
    ChecklistRemindersResponse,
    FellowChecklistItemResponse,
    FellowChecklistResponse,
)
from app.schemas.mentor import (
    ChecklistFeedbackMessageCreate,
    ChecklistFeedbackThreadResponse,
    ChecklistResponseSubmit,
)
from app.services.checklist import (
    get_fellow_checklist,
    get_fellow_checklist_reminders,
    require_visible_fellow_checklist_item,
    update_fellow_checklist_completion,
)
from app.schemas.session_media import (
    SessionRecordingPlaybackResponse,
    SessionTranscriptResponse,
)
from app.services.tldv import (
    get_session_recording_playback_for_fellow,
    get_session_transcript_for_fellow,
    stream_poc_seekable_recording_for_fellow,
    stream_seekable_recording_for_fellow,
)
from app.schemas.homepage import (
    FellowHomepageResponse,
    PhaseAccessVerificationResponse,
)
from app.services.homepage import (
    get_fellow_homepage,
    verify_fellow_phase_access,
)
router = APIRouter(prefix="/fellow", tags=["Fellow Portal"])


@router.get(
    "/homepage",
    response_model=FellowHomepageResponse,
    summary="Get Fellow homepage with 3-phase roadmap and progression access",
)
def get_homepage(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowHomepageResponse:
    return get_fellow_homepage(db, current_user)


@router.get(
    "/validate/overview",
    response_model=PhaseAccessVerificationResponse,
    summary="Access VALIDATE phase overview (protected by company shortlisting)",
)
def get_validate_overview(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> PhaseAccessVerificationResponse:
    return verify_fellow_phase_access(db, current_user, "VALIDATE")


@router.get(
    "/grow/overview",
    response_model=PhaseAccessVerificationResponse,
    summary="Access GROW phase overview (protected by company paid implementation approval)",
)
def get_grow_overview(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> PhaseAccessVerificationResponse:
    return verify_fellow_phase_access(db, current_user, "GROW")


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
    "/sessions/{session_id}/overview",
    response_model=SessionOverviewResponse,
    summary="Get global canonical session overview for authenticated Fellow",
)
def get_session_overview(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> SessionOverviewResponse:
    return get_fellow_session_overview(db, current_user, session_id)


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
    "/sessions/{session_id}/recording",
    response_model=SessionRecordingPlaybackResponse,
    summary="Get in-portal video recording playback for an unlocked Session",
)
def get_session_recording(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> SessionRecordingPlaybackResponse:
    return get_session_recording_playback_for_fellow(
        db,
        current_user,
        session_id,
    )


@router.get(
    "/sessions/{session_id}/seekable-stream",
    summary="Stream seekable MP4 recording with HTTP 206 byte-range support",
)
def stream_session_recording(
    session_id: UUID,
    range: str | None = Header(None, alias="Range"),
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> Response:
    return stream_seekable_recording_for_fellow(
        db,
        current_user,
        session_id,
        range_header=range,
    )


@router.get(
    "/sessions/{session_id}/poc-seekable-stream",
    summary="Stream local processed seekable MP4 recording with HTTP 206 byte-range support (Phase 7A POC)",
)
def stream_session_poc_recording(
    session_id: UUID,
    range: str | None = Header(None, alias="Range"),
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> Response:
    return stream_poc_seekable_recording_for_fellow(
        db,
        current_user,
        session_id,
        range_header=range,
    )


@router.get(
    "/sessions/{session_id}/transcript",
    response_model=SessionTranscriptResponse,
    summary="Get structured in-portal transcript for an unlocked Session",
)
def get_session_transcript(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> SessionTranscriptResponse:
    return get_session_transcript_for_fellow(
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
    "/sessions/{session_id}/working-board",
    response_model=FellowWorkingBoardResponse,
    summary="Get published Working Board for Fellow's team in a Session",
)
def get_session_working_board(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowWorkingBoardResponse:
    return get_fellow_working_board(
        db=db,
        current_user=current_user,
        session_id=session_id,
    )


@router.get(
    "/sessions/{session_id}/working-board-insights",
    response_model=FellowWorkingBoardInsightsResponse,
    summary="Get published Mentor Working Board Insights for Fellow's team in a Session",
)
def get_session_working_board_insights(
    session_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowWorkingBoardInsightsResponse:
    return get_fellow_working_board_insights(
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


@router.get(
    "/checklist-reminders",
    response_model=ChecklistRemindersResponse,
    summary="Get authenticated Fellow's actionable checklist reminders",
)
def get_checklist_reminders(
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> ChecklistRemindersResponse:
    return get_fellow_checklist_reminders(db, current_user)


@router.put(
    "/checklist/{item_id}/completion",
    response_model=FellowChecklistItemResponse,
    summary="Update the authenticated Fellow's checklist completion (with optional modal response)",
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


@router.put(
    "/checklist/{item_id}/response",
    response_model=FellowChecklistItemResponse,
    summary="Submit or update My Contribution and Reflection for a checklist item",
)
def submit_checklist_response(
    item_id: UUID,
    data: ChecklistResponseSubmit,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
) -> FellowChecklistItemResponse:
    """Called when the fellow fills in the modal and saves."""
    return update_fellow_checklist_completion(
        db,
        current_user,
        item_id,
        is_completed=True,
        contribution=data.contribution,
        reflection=data.reflection,
        my_thoughts=data.my_thoughts,
    )


@router.get(
    "/checklist/{item_id}/feedback",
    summary="Get checklist feedback thread for this Fellow + item",
)
def get_checklist_feedback_thread(
    item_id: UUID,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import get_feedback_thread
    require_visible_fellow_checklist_item(db, current_user, item_id)
    return get_feedback_thread(db, item_id=item_id, fellow_user_id=current_user.id)


@router.post(
    "/checklist/{item_id}/feedback",
    status_code=201,
    summary="Post a message in the checklist feedback thread",
)
def post_checklist_feedback_message(
    item_id: UUID,
    data: ChecklistFeedbackMessageCreate,
    current_user: User = Depends(require_fellow_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import post_feedback_message
    return post_feedback_message(
        db,
        item_id=item_id,
        fellow_user_id=current_user.id,
        author=current_user,
        message=data.message,
    )


# ===========================================================================
# DISCOVER -> VALIDATE Phase Gate – Fellow (read-only view)
# ===========================================================================

from app.services.phase_gate import (
    DISCOVER_TO_VALIDATE_GATE,
    get_gate_assessment_for_fellow,
)
from app.repositories.team import get_team_for_fellow
from app.repositories.enrollment import get_active_enrollment_for_user
from app.repositories.progression import get_team_phase_progression


@router.get(
    "/discover/gate-assessment",
    summary="Fellow read-only view of submitted DISCOVER->VALIDATE gate assessment",
)
def fellow_get_gate_assessment(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_fellow_portal),
):
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active enrollment found.",
        )
    team = get_team_for_fellow(db, current_user.id, enrollment.cohort_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found.",
        )
    # Check if VALIDATE has been approved for this team
    validate_progression = get_team_phase_progression(db, team.id, "VALIDATE")
    validate_unlocked = (
        validate_progression is not None
        and validate_progression.status == "approved"
    )

    result = get_gate_assessment_for_fellow(db, team.id, DISCOVER_TO_VALIDATE_GATE)
    if not result:
        return {
            "status": "not_submitted",
            "message": "Gate assessment not yet submitted by your mentor.",
            "validate_unlocked": validate_unlocked,
        }

    result["validate_unlocked"] = validate_unlocked
    return result
