"""
Mentor Portal API routes.

All endpoints require MENTOR or PROGRAM_MANAGER role (via require_mentor_portal).
Mentors may only access teams they are explicitly assigned to.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import require_mentor_portal, require_roles
from app.db.session import get_db
from app.models.checklist import (
    ChecklistFeedbackMessage,
    ChecklistItem,
    ChecklistReviewStatus,
    FellowChecklistCompletion,
    MentorTeamAssignment,
)
from app.models.cohort import Cohort
from app.models.feedback import SubmissionFeedback
from app.models.phase import Phase
from app.models.session import Session as DBSessionModel
from app.models.submission import TeamSubmission
from app.models.team import Team, TeamMembership
from app.models.user import User, UserRole
from app.models.week import Week as WeekModel
from app.schemas.feedback import SubmissionFeedbackDetail, SubmissionFeedbackUpsertRequest
from app.schemas.mentor import (
    ChecklistFeedbackMessageCreate,
    ChecklistFeedbackMessageResponse,
    ChecklistFeedbackThreadResponse,
    FellowChecklistCompletionDetail,
    MentorAssignedTeamsResponse,
    MentorChecklistReviewRequest,
    MentorNotificationsResponse,
    MentorProfileResponse,
    MentorSessionDetailResponse,
)
from app.schemas.checklist import (
    ChecklistActivationUpdate,
    MentorChecklistItemCreate,
    MentorChecklistItemResponse,
    MentorChecklistItemUpdate,
)
from app.schemas.submission import AdminSessionSubmissionItem, TeamSubmissionDetail
from app.services.mentor_checklist import (
    _require_checklist_item_visible_to_team,
    _require_fellow_in_team,
    get_feedback_thread,
    get_fellow_completion_for_mentor,
    get_mentor_assigned_teams,
    get_team_checklist_for_mentor,
    mentor_review_checklist_item,
    post_feedback_message,
    send_submission_insight_notification,
)
from app.services.checklist import (
    checklist_visibility_conditions,
    create_mentor_checklist_item,
    get_team_checklist_scope,
    list_mentor_checklist_items,
    set_mentor_checklist_item_activation,
    update_mentor_checklist_item,
)
from app.services.notifications import (
    get_unread_notification_count,
    list_portal_notifications,
    mark_all_portal_notifications_read,
    mark_portal_notification_read,
)
from app.schemas.working_board import (
    MentorWorkingBoardInsightsDetail,
    MentorWorkingBoardInsightsUpsertRequest,
    MentorWorkingBoardSummary,
)
from app.services.working_board import (
    get_mentor_working_board,
    get_mentor_working_board_insights,
    list_mentor_working_boards,
    publish_mentor_working_board_insights,
    upsert_mentor_working_board_insights,
)

router = APIRouter(prefix="", tags=["Mentor Portal"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth / profile
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=MentorProfileResponse,
    summary="Get the authenticated Mentor's profile",
)
def mentor_me(
    current_user: User = Depends(require_mentor_portal),
) -> MentorProfileResponse:
    return MentorProfileResponse.model_validate(current_user)


# ---------------------------------------------------------------------------
# Assigned teams
# ---------------------------------------------------------------------------


@router.get(
    "/teams",
    summary="List teams assigned to the authenticated Mentor (rich format)",
)
def list_assigned_teams(
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    assigned = get_mentor_assigned_teams(db, current_user)
    return _build_team_summaries(db, [t.team_id for t in assigned.teams])


@router.get(
    "/teams/{team_id}",
    summary="Get a single assigned team (rich format)",
)
def get_assigned_team(
    team_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, team_id)
    result = _build_team_summaries(db, [team_id])
    if not result:
        raise HTTPException(status_code=404, detail="Team not found.")
    return result[0]


def _build_team_summaries(db: Session, team_ids: list) -> list:
    """Build rich MentorTeamSummary objects matching the frontend type contract."""
    result = []
    for tid in team_ids:
        team = db.get(Team, tid)
        if not team or not team.is_active:
            continue
        cohort = db.get(Cohort, team.cohort_id)

        # Use the same progression resolver as Fellow checklist visibility.
        _, current_phase_id = get_team_checklist_scope(db, team.id)
        phase = db.get(Phase, current_phase_id) if current_phase_id else None
        current_phase = (
            {
                "id": str(phase.id),
                "code": phase.code,
                "name": phase.name,
                "development_role": phase.development_role,
                "sequence": phase.sequence,
            }
            if phase
            else None
        )

        # Members
        memberships = db.scalars(
            select(TeamMembership).where(TeamMembership.team_id == tid)
        ).all()
        members = []
        for m in memberships:
            fellow = db.get(User, m.user_id)
            if fellow:
                members.append({
                    "id": str(fellow.id),
                    "first_name": fellow.first_name,
                    "last_name": fellow.last_name,
                    "email": fellow.email,
                    "team_role": m.team_role.value if hasattr(m.team_role, 'value') else str(m.team_role),
                })

        result.append({
            "id": str(team.id),
            "team_id": str(team.id),
            "name": team.name,
            "team_name": team.name,
            "company_name": team.company_name if hasattr(team, 'company_name') else None,
            "company_challenge": team.company_challenge if hasattr(team, 'company_challenge') else None,
            "cohort": {
                "id": str(cohort.id),
                "name": cohort.name,
                "code": cohort.code if hasattr(cohort, 'code') else "",
                "status": cohort.status.value if hasattr(cohort.status, 'value') else str(cohort.status),
            } if cohort else {"id": "", "name": "Unknown", "code": "", "status": ""},
            "cohort_id": str(cohort.id) if cohort else "",
            "cohort_name": cohort.name if cohort else "Unknown",
            "current_phase": current_phase,
            "member_count": len(members),
            "members": members,
        })
    return result


# ---------------------------------------------------------------------------
# Mentor checklist definition management
# ---------------------------------------------------------------------------


@router.get(
    "/checklist-items",
    response_model=list[MentorChecklistItemResponse],
    summary="List checklist definitions applicable to assigned teams",
)
def mentor_list_checklist_items(
    team_id: UUID | None = None,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> list[MentorChecklistItemResponse]:
    return list_mentor_checklist_items(db, current_user, team_id)


@router.post(
    "/checklist-items",
    response_model=MentorChecklistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a team-scoped checklist item",
)
def mentor_create_checklist_item(
    data: MentorChecklistItemCreate,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorChecklistItemResponse:
    return create_mentor_checklist_item(db, current_user, data)


@router.put(
    "/checklist-items/{item_id}",
    response_model=MentorChecklistItemResponse,
    summary="Update a checklist item created by this Mentor",
)
def mentor_update_checklist_item(
    item_id: UUID,
    data: MentorChecklistItemUpdate,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorChecklistItemResponse:
    return update_mentor_checklist_item(db, current_user, item_id, data)


@router.patch(
    "/checklist-items/{item_id}/activation",
    response_model=MentorChecklistItemResponse,
    summary="Activate or deactivate a checklist item created by this Mentor",
)
def mentor_set_checklist_item_activation(
    item_id: UUID,
    data: ChecklistActivationUpdate,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorChecklistItemResponse:
    return set_mentor_checklist_item_activation(db, current_user, item_id, data)


# ---------------------------------------------------------------------------
# Team sessions (used by Working Board page to populate session dropdown)
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/sessions",
    summary="List sessions for an assigned team (by cohort)",
)
def get_sessions_for_team(
    team_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, team_id)

    team, _phase_id = get_team_checklist_scope(db, team_id)

    sessions = db.scalars(
        select(DBSessionModel)
        .where(DBSessionModel.cohort_id == team.cohort_id)
        .order_by(DBSessionModel.session_number.asc())
    ).all()

    result = []
    for s in sessions:
        week = db.get(WeekModel, s.week_id) if s.week_id else None
        result.append({
            "id": str(s.id),
            "session_number": s.session_number,
            "title": s.title,
            "description": s.description,
            "session_type": s.session_type.value if hasattr(s.session_type, "value") else str(s.session_type),
            "cohort_id": str(s.cohort_id),
            "phase_id": str(s.phase_id),
            "week_id": str(s.week_id) if s.week_id else None,
            "week_number": week.week_number if week else None,
            "week_title": week.title if week else None,
            "start_at": s.start_at.isoformat() if s.start_at else None,
            "end_at": s.end_at.isoformat() if s.end_at else None,
            "status": s.status.value if hasattr(s.status, "value") else str(s.status),
            "sequence": s.sequence,
            "has_recording": bool(s.recording_url or getattr(s, "recording_r2_key", None) or getattr(s, "tldv_meeting_id", None)),
            "has_transcript": bool(s.transcript_url or getattr(s, "tldv_meeting_id", None)),
            "meeting_url": s.meeting_url if s.is_unlocked else None,
            "is_unlocked": s.is_unlocked,
        })

    return {
        "team_id": str(team.id),
        "team_name": team.name,
        "sessions": result,
    }


@router.get(
    "/teams/{team_id}/sessions/{session_id}",
    response_model=MentorSessionDetailResponse,
    summary="Get session details for an assigned team by session ID",
)
def get_session_detail_for_team(
    team_id: UUID,
    session_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, team_id)

    team = db.get(Team, team_id)
    if not team or not team.is_active:
        raise HTTPException(status_code=404, detail="Team not found.")

    session = db.get(DBSessionModel, session_id)
    if not session or session.cohort_id != team.cohort_id:
        raise HTTPException(
            status_code=404,
            detail="Session not found for this team's cohort.",
        )

    cohort = db.get(Cohort, session.cohort_id)
    phase = db.get(Phase, session.phase_id) if session.phase_id else None
    week = db.get(WeekModel, session.week_id) if session.week_id else None

    # Suppress the meeting URL for locked sessions so Mentors cannot bypass
    # the unlock gate, but still allow them to view the session details.
    effective_meeting_url = session.meeting_url if session.is_unlocked else None

    return MentorSessionDetailResponse(
        id=session.id,
        cohort_id=session.cohort_id,
        cohort_name=cohort.name if cohort else None,
        phase_id=session.phase_id,
        phase_name=phase.name if phase else None,
        week_id=session.week_id,
        week_number=week.week_number if week else None,
        week_title=week.title if week else None,
        team_id=team.id,
        team_name=team.name,
        session_number=session.session_number,
        session_type=session.session_type.value if hasattr(session.session_type, "value") else str(session.session_type),
        title=session.title,
        description=session.description,
        start_at=session.start_at,
        end_at=session.end_at,
        unlock_at=session.unlock_at,
        submission_enabled=session.submission_enabled,
        meeting_url=effective_meeting_url,
        meeting_provider=session.meeting_provider,
        recording_url=session.recording_url,
        transcript_url=session.transcript_url,
        status=session.status.value if hasattr(session.status, "value") else str(session.status),
        sequence=session.sequence,
        is_unlocked=session.is_unlocked,
        has_recording=bool(session.recording_url or getattr(session, "recording_r2_key", None) or getattr(session, "tldv_meeting_id", None)),
        has_transcript=bool(session.transcript_url or getattr(session, "tldv_meeting_id", None)),
    )


# ---------------------------------------------------------------------------
# Team checklist overview
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/checklist",
    summary="View all fellows' checklist responses for an assigned team (rich flat array)",
)
def team_checklist_overview(
    team_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    from app.models.session import Session as DBSessionModel
    from app.models.week import Week as WeekModel
    from app.models.cohort import Cohort

    _require_mentor_assigned_to_team(db, current_user.id, team_id)

    team, phase_id = get_team_checklist_scope(db, team_id)

    cohort = db.get(Cohort, team.cohort_id)
    cohort_name = cohort.name if cohort else None

    memberships = db.scalars(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).all()

    # Use the same global/cohort/phase/week/session visibility rules as the
    # Fellow Portal so mentors review exactly what their fellows can see.
    items = db.scalars(
        select(ChecklistItem)
        .where(*checklist_visibility_conditions(team.cohort_id, phase_id, team.id))
        .order_by(ChecklistItem.sequence, ChecklistItem.created_at)
    ).all()

    result = []
    for membership in memberships:
        fellow = db.get(User, membership.user_id)
        if not fellow:
            continue
        fellow_name = f"{fellow.first_name} {fellow.last_name}".strip()

        for item in items:
            completion = db.scalar(
                select(FellowChecklistCompletion).where(
                    FellowChecklistCompletion.checklist_item_id == item.id,
                    FellowChecklistCompletion.user_id == fellow.id,
                )
            )

            # Session / week info
            session_obj = db.get(DBSessionModel, item.session_id) if item.session_id else None
            week_obj = db.get(WeekModel, session_obj.week_id) if session_obj and session_obj.week_id else None

            result.append({
                "id": f"{item.id}:{fellow.id}",
                "checklist_item_id": str(item.id),
                "title": item.title,
                "description": item.description,
                "category": item.category,
                "due_at": item.due_at.isoformat() if item.due_at else None,
                "is_required": item.is_required,
                "is_completed": completion.is_completed if completion else False,
                "completed_at": completion.completed_at.isoformat() if completion and completion.completed_at else None,
                "sequence": item.sequence,
                # Fellow response
                "contribution": completion.contribution if completion else None,
                "reflection": completion.reflection if completion else None,
                "my_thoughts": completion.my_thoughts if completion else None,
                # Review workflow
                "review_status": (
                    completion.review_status.value if completion else "not_checked"
                ),
                "rejection_reason": completion.rejection_reason if completion else None,
                "reviewed_at": completion.reviewed_at.isoformat() if completion and completion.reviewed_at else None,
                # Scope context
                "session_id": str(item.session_id) if item.session_id else None,
                "session_name": session_obj.title if session_obj else None,
                "week_number": week_obj.week_number if week_obj else None,
                "cohort_name": cohort_name,
                # Fellow info
                "fellow_id": str(fellow.id),
                "fellow_name": fellow_name,
                "team_id": str(team.id),
                "team_name": team.name,
            })

    return result


# ---------------------------------------------------------------------------
# Individual fellow checklist completion detail
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/checklist/{checklist_item_id}/fellows/{fellow_user_id}",
    response_model=FellowChecklistCompletionDetail | None,
    summary="Get a specific fellow's checklist response detail",
)
def fellow_completion_detail(
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    completion = get_fellow_completion_for_mentor(
        db, current_user, team_id, checklist_item_id, fellow_user_id
    )
    if not completion:
        return None
    return FellowChecklistCompletionDetail.model_validate(completion)


# ---------------------------------------------------------------------------
# Mentor review – accept / reject
# ---------------------------------------------------------------------------


@router.post(
    "/teams/{team_id}/checklist/{checklist_item_id}/fellows/{fellow_user_id}/review",
    response_model=FellowChecklistCompletionDetail,
    summary="Accept or reject a fellow's checklist response",
)
def review_checklist_item(
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
    data: MentorChecklistReviewRequest,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> FellowChecklistCompletionDetail:
    completion = mentor_review_checklist_item(
        db=db,
        mentor=current_user,
        team_id=team_id,
        checklist_item_id=checklist_item_id,
        fellow_user_id=fellow_user_id,
        action=data.action,
        rejection_reason=data.rejection_reason,
    )
    return FellowChecklistCompletionDetail.model_validate(completion)


# ---------------------------------------------------------------------------
# Flat convenience review endpoint – matches frontend API client
# POST /mentor/checklist/{item_id}/review
# Body: { "decision": "accepted"|"rejected", "rejection_reason"?: str,
#         "fellow_id"?: UUID }
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Checklist Feedback – read thread
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/checklist/{checklist_item_id}/fellows/{fellow_user_id}/feedback",
    response_model=ChecklistFeedbackThreadResponse,
    summary="Get the checklist feedback thread for a fellow+item",
)
def get_checklist_feedback(
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> ChecklistFeedbackThreadResponse:
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, team_id)
    _require_fellow_in_team(db, team_id, fellow_user_id)
    _require_checklist_item_visible_to_team(db, team_id, checklist_item_id)
    return get_feedback_thread(db, item_id=checklist_item_id, fellow_user_id=fellow_user_id)


# ---------------------------------------------------------------------------
# Checklist Feedback – post message
# ---------------------------------------------------------------------------


@router.post(
    "/teams/{team_id}/checklist/{checklist_item_id}/fellows/{fellow_user_id}/feedback",
    response_model=ChecklistFeedbackMessageResponse,
    status_code=201,
    summary="Post a message in the checklist feedback thread",
)
def post_checklist_feedback(
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
    data: ChecklistFeedbackMessageCreate,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> ChecklistFeedbackMessageResponse:
    return post_feedback_message(
        db=db,
        item_id=checklist_item_id,
        fellow_user_id=fellow_user_id,
        author=current_user,
        message=data.message,
        team_id=team_id,
    )


# ---------------------------------------------------------------------------
# Submission Insights – view submissions
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}/submissions",
    summary="List document submissions for an assigned team",
)
def list_team_submissions(
    team_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, team_id)

    submissions = db.scalars(
        select(TeamSubmission)
        .where(TeamSubmission.team_id == team_id)
        .order_by(TeamSubmission.submitted_at.desc())
    ).all()

    return [TeamSubmissionDetail.model_validate(s) for s in submissions]


# ---------------------------------------------------------------------------
# Submission Insights – get/write feedback (Submission Insights)
# ---------------------------------------------------------------------------


@router.get(
    "/submissions/{submission_id}/insights",
    response_model=SubmissionFeedbackDetail | None,
    summary="Get Submission Insights for a document",
)
def get_submission_insights(
    submission_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
):
    submission = db.get(TeamSubmission, submission_id)
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found.",
        )
    # Verify mentor is assigned to this team
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, submission.team_id)

    feedback = db.scalar(
        select(SubmissionFeedback).where(
            SubmissionFeedback.submission_id == submission_id
        )
    )
    return SubmissionFeedbackDetail.model_validate(feedback) if feedback else None


@router.put(
    "/submissions/{submission_id}/insights",
    response_model=SubmissionFeedbackDetail,
    summary="Create or update Submission Insights (document evaluation)",
)
def upsert_submission_insights(
    submission_id: UUID,
    data: SubmissionFeedbackUpsertRequest,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> SubmissionFeedbackDetail:
    submission = db.get(TeamSubmission, submission_id)
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found.",
        )
    from app.services.mentor_checklist import _require_mentor_assigned_to_team
    _require_mentor_assigned_to_team(db, current_user.id, submission.team_id)

    feedback_text = data.feedback_text.strip()
    if not feedback_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Feedback text cannot be empty.",
        )
    feedback_url = data.feedback_url.strip() if data.feedback_url else None

    feedback = db.scalar(
        select(SubmissionFeedback).where(
            SubmissionFeedback.submission_id == submission_id
        )
    )

    is_new = feedback is None
    if feedback:
        feedback.feedback_text = feedback_text
        feedback.feedback_url = feedback_url
        feedback.status = data.status
        feedback.reviewed_by_user_id = current_user.id
    else:
        feedback = SubmissionFeedback(
            submission_id=submission_id,
            reviewed_by_user_id=current_user.id,
            feedback_text=feedback_text,
            feedback_url=feedback_url,
            status=data.status,
        )
        db.add(feedback)

    db.commit()
    db.refresh(feedback)

    # Notify team members (Trigger 3)
    if is_new:
        try:
            send_submission_insight_notification(
                db=db,
                team_id=submission.team_id,
                reviewer=current_user,
            )
        except Exception:
            logger.exception(
                "Failed to notify fellows of submission insights for team=%s",
                submission.team_id,
            )

    return SubmissionFeedbackDetail.model_validate(feedback)


# ---------------------------------------------------------------------------
# Working Board & Working Board Insights
# ---------------------------------------------------------------------------


@router.get(
    "/working-boards",
    response_model=list[MentorWorkingBoardSummary],
    summary="List Working Boards for teams assigned to the authenticated mentor",
)
def mentor_list_working_boards(
    session_id: UUID | None = None,
    team_id: UUID | None = None,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> list[MentorWorkingBoardSummary]:
    return list_mentor_working_boards(
        db=db,
        current_user=current_user,
        session_id=session_id,
        team_id=team_id,
    )


@router.get(
    "/working-boards/{board_id}",
    response_model=MentorWorkingBoardSummary,
    summary="Get single Working Board details for assigned team",
)
def mentor_get_working_board(
    board_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorWorkingBoardSummary:
    return get_mentor_working_board(
        db=db,
        current_user=current_user,
        board_id=board_id,
    )


@router.get(
    "/working-boards/{board_id}/insights",
    response_model=MentorWorkingBoardInsightsDetail | None,
    summary="Get mentor Working Board Insights (draft or published)",
)
def mentor_get_working_board_insights(
    board_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorWorkingBoardInsightsDetail | None:
    return get_mentor_working_board_insights(
        db=db,
        current_user=current_user,
        board_id=board_id,
    )


@router.put(
    "/working-boards/{board_id}/insights",
    response_model=MentorWorkingBoardInsightsDetail,
    summary="Save draft or published Working Board Insights",
)
def mentor_upsert_working_board_insights(
    board_id: UUID,
    data: MentorWorkingBoardInsightsUpsertRequest,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorWorkingBoardInsightsDetail:
    return upsert_mentor_working_board_insights(
        db=db,
        current_user=current_user,
        board_id=board_id,
        data=data,
    )


@router.post(
    "/working-boards/{board_id}/insights/publish",
    response_model=MentorWorkingBoardInsightsDetail,
    summary="Publish Working Board Insights for the team",
)
def mentor_publish_working_board_insights(
    board_id: UUID,
    current_user: User = Depends(require_mentor_portal),
    db: Session = Depends(get_db),
) -> MentorWorkingBoardInsightsDetail:
    return publish_mentor_working_board_insights(
        db=db,
        current_user=current_user,
        board_id=board_id,
    )


# ===========================================================================
# MENTOR HOME, SESSIONS, NOTIFICATIONS & SESSION CHECKLIST ITEMS
# ===========================================================================


@router.get("/home", summary="Get mentor portal dashboard metrics and pending tasks")
def get_mentor_home(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    assigned = get_mentor_assigned_teams(db, current_user)
    team_ids = [t.team_id for t in assigned.teams]
    mentor_info = {
        "id": str(current_user.id),
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "role": current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role),
    }

    if not team_ids:
        return {
            "mentor": mentor_info,
            "assigned_teams": [],
            "pending_reviews": [],
            "pending_checklist_reviews": [],
            "pending_document_reviews": [],
            "recent_fellow_comments": [],
            "recent_activity": [],
            "unread_notifications": get_unread_notification_count(db, current_user.id),
        }

    # 1. Assigned teams detail
    teams_data = _build_team_summaries(db, team_ids)

    # 2. Pending checklist reviews
    # Fellows in assigned teams
    fellow_ids = db.scalars(
        select(TeamMembership.user_id).where(TeamMembership.team_id.in_(team_ids))
    ).all()

    pending_checklist = []
    if fellow_ids:
        completions = db.scalars(
            select(FellowChecklistCompletion)
            .where(
                FellowChecklistCompletion.user_id.in_(fellow_ids),
                FellowChecklistCompletion.is_completed == True,
                FellowChecklistCompletion.review_status == ChecklistReviewStatus.PENDING_REVIEW,
            )
            .order_by(FellowChecklistCompletion.completed_at.desc())
            .limit(20)
        ).all()

        for c in completions:
            item = db.get(ChecklistItem, c.checklist_item_id)
            fellow = db.get(User, c.user_id)
            tm = db.scalar(select(TeamMembership).where(TeamMembership.user_id == c.user_id))
            team = db.get(Team, tm.team_id) if tm else None
            session_obj = db.get(DBSessionModel, item.session_id) if item and item.session_id else None
            pending_checklist.append({
                "completion_id": str(c.id),
                "fellow_user_id": str(c.user_id),
                "fellow_name": f"{fellow.first_name} {fellow.last_name}".strip() if fellow else "Fellow",
                "team_id": str(team.id) if team else "",
                "team_name": team.name if team else "Team",
                "checklist_item_id": str(c.checklist_item_id),
                "checklist_item_title": item.title if item else "Checklist item",
                "session_name": session_obj.title if session_obj else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                "contribution": c.contribution,
                "reflection": c.reflection,
                "my_thoughts": c.my_thoughts,
            })

    # 3. Pending document reviews
    submissions = db.scalars(
        select(TeamSubmission)
        .where(TeamSubmission.team_id.in_(team_ids))
        .order_by(TeamSubmission.submitted_at.desc())
        .limit(20)
    ).all()

    pending_docs = []
    for s in submissions:
        fb = db.scalar(select(SubmissionFeedback).where(SubmissionFeedback.submission_id == s.id))
        if not fb or fb.status == "revision_required":
            team = db.get(Team, s.team_id)
            session_obj = db.get(DBSessionModel, s.session_id)
            cohort = db.get(Cohort, team.cohort_id) if team else None
            pending_docs.append({
                "submission_id": str(s.id),
                "team_id": str(s.team_id),
                "team_name": team.name if team else "Team",
                "cohort_name": cohort.name if cohort else "Cohort",
                "session_id": str(s.session_id),
                "session_name": session_obj.title if session_obj else "Session",
                "drive_url": s.drive_url,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
                "review_status": fb.status if fb else "unreviewed",
            })

    from app.models.working_board import WorkingBoard, WorkingBoardInsights
    working_boards = db.scalars(
        select(WorkingBoard)
        .where(
            WorkingBoard.team_id.in_(team_ids),
            WorkingBoard.is_published.is_(True),
        )
        .order_by(WorkingBoard.created_at.desc())
        .limit(20)
    ).all()
    for wb in working_boards:
        ins = db.scalar(select(WorkingBoardInsights).where(WorkingBoardInsights.working_board_id == wb.id))
        if not ins or ins.publication_status != "published":
            team = db.get(Team, wb.team_id)
            session_obj = db.get(DBSessionModel, wb.session_id)
            cohort = db.get(Cohort, team.cohort_id) if team else None
            pending_docs.append({
                "submission_id": str(wb.id),
                "working_board_id": str(wb.id),
                "team_id": str(wb.team_id),
                "team_name": team.name if team else "Team",
                "cohort_name": cohort.name if cohort else "Cohort",
                "session_id": str(wb.session_id),
                "session_name": session_obj.title if session_obj else "Session",
                "drive_url": wb.working_board_url,
                "submitted_at": wb.created_at.isoformat() if wb.created_at else None,
                "review_status": ins.publication_status if ins else "unreviewed",
                "is_working_board": True,
            })

    # 4. Recent comments
    recent_comments = []
    if fellow_ids:
        msgs = db.scalars(
            select(ChecklistFeedbackMessage)
            .where(
                ChecklistFeedbackMessage.author_role == "fellow",
                ChecklistFeedbackMessage.fellow_user_id.in_(fellow_ids),
            )
            .order_by(ChecklistFeedbackMessage.created_at.desc())
            .limit(10)
        ).all()
        for m in msgs:
            fellow = db.get(User, m.author_user_id)
            item = db.get(ChecklistItem, m.checklist_item_id)
            tm = db.scalar(select(TeamMembership).where(TeamMembership.user_id == m.author_user_id))
            team = db.get(Team, tm.team_id) if tm else None
            recent_comments.append({
                "id": str(m.id),
                "fellow_user_id": str(m.fellow_user_id),
                "fellow_name": f"{fellow.first_name} {fellow.last_name}".strip() if fellow else "Fellow",
                "team_id": str(team.id) if team else "",
                "team_name": team.name if team else "Team",
                "checklist_item_id": str(m.checklist_item_id),
                "checklist_item_title": item.title if item else "Checklist item",
                "message": m.message,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })

    # Compute pending reviews summary per team for frontend dashboard
    pending_counts_by_team = {}
    for c in pending_checklist:
        t_id = c.get("team_id")
        t_name = c.get("team_name")
        if t_id:
            if t_id not in pending_counts_by_team:
                pending_counts_by_team[t_id] = {"team_id": t_id, "team_name": t_name, "pending_count": 0}
            pending_counts_by_team[t_id]["pending_count"] += 1

    return {
        "mentor": mentor_info,
        "assigned_teams": teams_data,
        "pending_reviews": list(pending_counts_by_team.values()),
        "pending_checklist_reviews": pending_checklist,
        "pending_document_reviews": pending_docs,
        "recent_fellow_comments": recent_comments,
        "unread_notifications": get_unread_notification_count(db, current_user.id),
    }


@router.get(
    "/notifications",
    response_model=MentorNotificationsResponse,
    summary="List persistent mentor notifications",
)
def get_mentor_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
) -> MentorNotificationsResponse:
    return list_portal_notifications(db, current_user.id)


@router.post(
    "/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark one mentor notification as read",
)
def mark_mentor_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
) -> None:
    mark_portal_notification_read(db, current_user.id, notification_id)


@router.post(
    "/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark all mentor notifications as read",
)
def mark_all_mentor_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
) -> None:
    mark_all_portal_notifications_read(db, current_user.id)


# ---------------------------------------------------------------------------
# Sessions & Checklist Item Management for Mentors
# ---------------------------------------------------------------------------


def _require_mentor_for_cohort(
    db: Session,
    mentor_id: UUID,
    cohort_id: UUID,
) -> None:
    assignment = db.scalar(
        select(MentorTeamAssignment).where(
            MentorTeamAssignment.mentor_user_id == mentor_id,
            MentorTeamAssignment.cohort_id == cohort_id,
        )
    )
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to a team in this cohort.",
        )

@router.get("/sessions", summary="List sessions accessible to this mentor")
def get_mentor_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    assigned = get_mentor_assigned_teams(db, current_user)
    cohort_ids = list({t.cohort_id for t in assigned.teams})
    if not cohort_ids:
        return []

    sessions = db.scalars(
        select(DBSessionModel)
        .where(DBSessionModel.cohort_id.in_(cohort_ids))
        .order_by(DBSessionModel.session_number.asc())
    ).all()

    result = []
    for s in sessions:
        cohort = db.get(Cohort, s.cohort_id)
        week = db.get(WeekModel, s.week_id) if s.week_id else None
        item_count = db.scalar(
            select(func.count(ChecklistItem.id)).where(ChecklistItem.session_id == s.id)
        ) or 0
        result.append({
            "id": str(s.id),
            "session_number": s.session_number,
            "title": s.title,
            "description": s.description,
            "session_type": s.session_type.value if hasattr(s.session_type, "value") else str(s.session_type),
            "cohort_id": str(s.cohort_id),
            "cohort_name": cohort.name if cohort else "Cohort",
            "week_id": str(s.week_id) if s.week_id else None,
            "week_number": week.week_number if week else None,
            "week_title": week.title if week else None,
            "checklist_item_count": item_count,
        })
    return result


@router.get("/sessions/{session_id}/checklist-items", summary="Get checklist items for a session")
def get_session_checklist_items(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    session_obj = db.get(DBSessionModel, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found.")
    _require_mentor_for_cohort(db, current_user.id, session_obj.cohort_id)
    items = db.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.session_id == session_id)
        .order_by(ChecklistItem.sequence.asc(), ChecklistItem.created_at.asc())
    ).all()
    return [
        {
            "id": str(it.id),
            "session_id": str(it.session_id) if it.session_id else None,
            "cohort_id": str(it.cohort_id) if it.cohort_id else None,
            "title": it.title,
            "description": it.description,
            "category": it.category,
            "due_at": it.due_at.isoformat() if it.due_at else None,
            "is_required": it.is_required,
            "is_active": it.is_active,
            "sequence": it.sequence,
        }
        for it in items
    ]



# ===========================================================================
# DISCOVER -> VALIDATE Phase Gate Assessment (Mentor)
# ===========================================================================

from app.services.phase_gate import (
    DISCOVER_TO_VALIDATE_GATE,
    DISCOVER_TO_VALIDATE_DIMENSIONS,
    get_or_create_gate_assessment,
    save_gate_assessment_draft,
    submit_gate_assessment,
    serialize_assessment,
)
from app.models.checklist import MentorTeamAssignment as _MTA


def _require_mentor_owns_team(db: Session, mentor_id: UUID, team_id: UUID) -> None:
    """Guard: mentor must be assigned to this team."""
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")
    assignment = db.scalar(
        select(_MTA).where(
            _MTA.mentor_user_id == mentor_id,
            _MTA.cohort_id == team.cohort_id,
        )
    )
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this team.",
        )


@router.get(
    "/teams/{team_id}/phase-gate",
    summary="Get DISCOVER->VALIDATE gate assessment for a team (Mentor view)",
)
def mentor_get_phase_gate(
    team_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    _require_mentor_owns_team(db, current_user.id, team_id)
    assessment = get_or_create_gate_assessment(
        db, team_id, DISCOVER_TO_VALIDATE_GATE, current_user.id
    )
    result = serialize_assessment(assessment)
    result["rubric"] = DISCOVER_TO_VALIDATE_DIMENSIONS
    return result


@router.put(
    "/teams/{team_id}/phase-gate/draft",
    summary="Save DISCOVER->VALIDATE gate assessment draft (Mentor)",
)
def mentor_save_gate_draft(
    team_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    _require_mentor_owns_team(db, current_user.id, team_id)
    assessment = save_gate_assessment_draft(
        db,
        team_id,
        DISCOVER_TO_VALIDATE_GATE,
        current_user.id,
        payload.get("dimensions", []),
        payload.get("mentor_notes"),
    )
    return serialize_assessment(assessment)


@router.post(
    "/teams/{team_id}/phase-gate/submit",
    summary="Submit DISCOVER->VALIDATE gate assessment (Mentor - final)",
    status_code=status.HTTP_200_OK,
)
def mentor_submit_gate(
    team_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_mentor_portal),
):
    _require_mentor_owns_team(db, current_user.id, team_id)
    assessment = submit_gate_assessment(
        db,
        team_id,
        DISCOVER_TO_VALIDATE_GATE,
        current_user.id,
        payload.get("dimensions", []),
        payload.get("mentor_notes"),
    )
    return serialize_assessment(assessment)
