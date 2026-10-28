"""
Working Board & Working Board Insights service.

Implements:
- Admin Working Board management (CRUD + publication)
- Fellow Working Board view & published Insights view
- Mentor Working Board access, draft/publish Insights with audit revision history
- Fellow notification upon publication of Insights
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cohort import Cohort
from app.models.session import Session as DBSession
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.models.week import Week
from app.models.working_board import (
    WorkingBoard,
    WorkingBoardInsightRevision,
    WorkingBoardInsights,
)
from app.repositories.discover import get_session_by_id_for_cohort
from app.repositories.enrollment import get_active_enrollment_for_user
from app.repositories.team import get_team_membership_for_cohort
from app.schemas.working_board import (
    AdminWorkingBoardCreateRequest,
    AdminWorkingBoardDetail,
    AdminWorkingBoardUpdateRequest,
    FellowWorkingBoardInsightsResponse,
    FellowWorkingBoardResponse,
    MentorWorkingBoardInsightsDetail,
    MentorWorkingBoardInsightsUpsertRequest,
    MentorWorkingBoardSummary,
    validate_google_docs_url,
)
from app.services.discover import require_session_unlocked
from app.services.email import send_email
from app.services.mentor_checklist import _get_assignment, get_mentor_assigned_teams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification Helper
# ---------------------------------------------------------------------------


def send_working_board_insights_notification(
    db: Session,
    team_id: UUID,
    mentor: User,
    session_title: str | None = None,
) -> None:
    """
    Notify all team fellows when a Mentor publishes Working Board Insights.
    Includes only safe contextual info and a portal link, avoiding confidential text.
    """
    memberships = db.scalars(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).all()

    team = db.get(Team, team_id)
    team_name = team.name if team else "your team"
    session_ctx = f" for {session_title}" if session_title else ""
    mentor_name = f"{mentor.first_name} {mentor.last_name}".strip()

    notified: set[UUID] = set()
    for tm in memberships:
        if tm.user_id in notified:
            continue
        notified.add(tm.user_id)
        fellow = db.get(User, tm.user_id)
        if not fellow:
            continue

        subject = f"DLIF Working Board Insights: {team_name}"
        text_body = (
            f"Hello {fellow.first_name},\n\n"
            f"Your team's mentor, {mentor_name}, has published Working Board Insights{session_ctx}.\n\n"
            f"Please log in to your DLIF Fellow Portal to view the insights.\n\n"
            f"— DegreeLabs DLIF Team"
        )
        html_body = f"""
        <p>Hello {fellow.first_name},</p>
        <p>Your team's mentor, <strong>{mentor_name}</strong>, has published Working Board Insights{session_ctx} for <strong>{team_name}</strong>.</p>
        <p>Please log in to your DLIF Fellow Portal to view the insights.</p>
        <p>— DegreeLabs DLIF Team</p>
        """
        try:
            send_email(
                to_address=fellow.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except Exception:
            logger.exception(
                "Failed to send working board insights notification to fellow %s",
                fellow.id,
            )


# ---------------------------------------------------------------------------
# Admin Service Functions
# ---------------------------------------------------------------------------


def _enrich_admin_board(db: Session, board: WorkingBoard) -> AdminWorkingBoardDetail:
    session_obj = db.get(DBSession, board.session_id)
    team_obj = db.get(Team, board.team_id)
    cohort_obj = db.get(Cohort, team_obj.cohort_id) if team_obj else None
    week_obj = db.get(Week, session_obj.week_id) if session_obj and session_obj.week_id else None

    return AdminWorkingBoardDetail(
        id=board.id,
        session_id=board.session_id,
        team_id=board.team_id,
        title=board.title,
        working_board_url=board.working_board_url,
        instructions=board.instructions,
        is_published=board.is_published,
        created_by_user_id=board.created_by_user_id,
        updated_by_user_id=board.updated_by_user_id,
        created_at=board.created_at,
        updated_at=board.updated_at,
        session_title=session_obj.title if session_obj else None,
        session_number=session_obj.session_number if session_obj else None,
        team_name=team_obj.name if team_obj else None,
        cohort_id=cohort_obj.id if cohort_obj else None,
        cohort_name=cohort_obj.name if cohort_obj else None,
        week_number=week_obj.week_number if week_obj else None,
    )


def list_admin_working_boards(
    db: Session,
    session_id: UUID | None = None,
    team_id: UUID | None = None,
    cohort_id: UUID | None = None,
) -> list[AdminWorkingBoardDetail]:
    query = select(WorkingBoard)
    if session_id:
        query = query.where(WorkingBoard.session_id == session_id)
    if team_id:
        query = query.where(WorkingBoard.team_id == team_id)
    if cohort_id:
        query = query.join(Team, WorkingBoard.team_id == Team.id).where(Team.cohort_id == cohort_id)

    query = query.order_by(WorkingBoard.created_at.desc())
    boards = db.scalars(query).all()
    return [_enrich_admin_board(db, b) for b in boards]


def create_admin_working_board(
    db: Session,
    admin_user: User,
    data: AdminWorkingBoardCreateRequest,
) -> AdminWorkingBoardDetail:
    # 1. Validate session and team existence
    session_obj = db.get(DBSession, data.session_id)
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    team_obj = db.get(Team, data.team_id)
    if not team_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found.",
        )

    # 2. Validate cohort relationship
    if team_obj.cohort_id != session_obj.cohort_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Selected team and session do not belong to the same cohort.",
        )

    # 3. Prevent duplicate Working Board for the same session and team
    existing = db.scalar(
        select(WorkingBoard).where(
            WorkingBoard.session_id == data.session_id,
            WorkingBoard.team_id == data.team_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Working Board already exists for this team and session.",
        )

    # 4. Create record
    board = WorkingBoard(
        session_id=data.session_id,
        team_id=data.team_id,
        title=data.title,
        working_board_url=data.working_board_url,
        instructions=data.instructions,
        is_published=data.is_published,
        created_by_user_id=admin_user.id,
        updated_by_user_id=admin_user.id,
    )
    db.add(board)
    db.commit()
    db.refresh(board)

    return _enrich_admin_board(db, board)


def get_admin_working_board(
    db: Session,
    board_id: UUID,
) -> AdminWorkingBoardDetail:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )
    return _enrich_admin_board(db, board)


def update_admin_working_board(
    db: Session,
    admin_user: User,
    board_id: UUID,
    data: AdminWorkingBoardUpdateRequest,
) -> AdminWorkingBoardDetail:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    if data.title is not None:
        board.title = data.title
    if data.working_board_url is not None:
        board.working_board_url = data.working_board_url
    if data.instructions is not None:
        board.instructions = data.instructions
    if data.is_published is not None:
        board.is_published = data.is_published

    board.updated_by_user_id = admin_user.id
    db.commit()
    db.refresh(board)

    return _enrich_admin_board(db, board)


def patch_admin_working_board_publication(
    db: Session,
    admin_user: User,
    board_id: UUID,
    is_published: bool,
) -> AdminWorkingBoardDetail:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    board.is_published = is_published
    board.updated_by_user_id = admin_user.id
    db.commit()
    db.refresh(board)

    return _enrich_admin_board(db, board)


# ---------------------------------------------------------------------------
# Fellow Service Functions
# ---------------------------------------------------------------------------


def get_fellow_working_board(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> FellowWorkingBoardResponse:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active enrollment found for this Fellow.",
        )

    session = get_session_by_id_for_cohort(db, session_id, enrollment.cohort_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    require_session_unlocked(session)

    membership = get_team_membership_for_cohort(
        db,
        enrollment.cohort_id,
        current_user.id,
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Team assignment found for this Fellow.",
        )

    if not membership.team.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Fellow's Team is inactive.",
        )

    board = db.scalar(
        select(WorkingBoard).where(
            WorkingBoard.session_id == session_id,
            WorkingBoard.team_id == membership.team_id,
            WorkingBoard.is_published.is_(True),
        )
    )

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published Working Board found for your team in this session.",
        )

    return FellowWorkingBoardResponse(
        id=board.id,
        session_id=board.session_id,
        team_id=board.team_id,
        title=board.title,
        working_board_url=board.working_board_url,
        instructions=board.instructions,
        is_published=board.is_published,
        updated_at=board.updated_at,
        cta_text="Click here to open the Working Board and work collaboratively.",
    )


def get_fellow_working_board_insights(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> FellowWorkingBoardInsightsResponse:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active enrollment found for this Fellow.",
        )

    session = get_session_by_id_for_cohort(db, session_id, enrollment.cohort_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    require_session_unlocked(session)

    membership = get_team_membership_for_cohort(
        db,
        enrollment.cohort_id,
        current_user.id,
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Team assignment found for this Fellow.",
        )

    board = db.scalar(
        select(WorkingBoard).where(
            WorkingBoard.session_id == session_id,
            WorkingBoard.team_id == membership.team_id,
            WorkingBoard.is_published.is_(True),
        )
    )
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published Working Board found for your team in this session.",
        )

    insight = db.scalar(
        select(WorkingBoardInsights).where(
            WorkingBoardInsights.working_board_id == board.id,
            WorkingBoardInsights.publication_status == "published",
        )
    )
    if not insight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published Working Board Insights found for your team in this session.",
        )

    mentor = db.get(User, insight.mentor_user_id)
    mentor_name = f"{mentor.first_name} {mentor.last_name}".strip() if mentor else None

    return FellowWorkingBoardInsightsResponse(
        id=insight.id,
        working_board_id=insight.working_board_id,
        session_id=board.session_id,
        team_id=board.team_id,
        mentor_user_id=insight.mentor_user_id,
        mentor_name=mentor_name,
        insights_text=insight.insights_text,
        publication_status=insight.publication_status,
        tagline="Insights provided by the Team's Mentor.",
        published_at=insight.published_at,
        created_at=insight.created_at,
        updated_at=insight.updated_at,
    )


# ---------------------------------------------------------------------------
# Mentor Service Functions
# ---------------------------------------------------------------------------


def _require_mentor_for_team(db: Session, mentor_id: UUID, team_id: UUID) -> None:
    if not _get_assignment(db, mentor_id, team_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this team.",
        )


def _enrich_mentor_board(db: Session, board: WorkingBoard) -> MentorWorkingBoardSummary:
    session_obj = db.get(DBSession, board.session_id)
    team_obj = db.get(Team, board.team_id)
    cohort_obj = db.get(Cohort, team_obj.cohort_id) if team_obj else None

    insight = db.scalar(
        select(WorkingBoardInsights).where(
            WorkingBoardInsights.working_board_id == board.id
        )
    )

    return MentorWorkingBoardSummary(
        id=board.id,
        session_id=board.session_id,
        session_title=session_obj.title if session_obj else None,
        session_number=session_obj.session_number if session_obj else None,
        team_id=board.team_id,
        team_name=team_obj.name if team_obj else None,
        cohort_id=cohort_obj.id if cohort_obj else None,
        cohort_name=cohort_obj.name if cohort_obj else None,
        title=board.title,
        working_board_url=board.working_board_url,
        instructions=board.instructions,
        is_published=board.is_published,
        has_insights=insight is not None,
        insights_status=insight.publication_status if insight else None,
        updated_at=board.updated_at,
    )


def list_mentor_working_boards(
    db: Session,
    current_user: User,
    session_id: UUID | None = None,
    team_id: UUID | None = None,
) -> list[MentorWorkingBoardSummary]:
    assigned = get_mentor_assigned_teams(db, current_user)
    assigned_team_ids = {t.team_id for t in assigned.teams}

    if team_id:
        if team_id not in assigned_team_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this team.",
            )
        target_team_ids = [team_id]
    else:
        target_team_ids = list(assigned_team_ids)

    if not target_team_ids:
        return []

    query = select(WorkingBoard).where(WorkingBoard.team_id.in_(target_team_ids))
    if session_id:
        query = query.where(WorkingBoard.session_id == session_id)

    query = query.order_by(WorkingBoard.created_at.desc())
    boards = db.scalars(query).all()
    return [_enrich_mentor_board(db, b) for b in boards]


def get_mentor_working_board(
    db: Session,
    current_user: User,
    board_id: UUID,
) -> MentorWorkingBoardSummary:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    _require_mentor_for_team(db, current_user.id, board.team_id)
    return _enrich_mentor_board(db, board)


def get_mentor_working_board_insights(
    db: Session,
    current_user: User,
    board_id: UUID,
) -> MentorWorkingBoardInsightsDetail | None:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    _require_mentor_for_team(db, current_user.id, board.team_id)

    insight = db.scalar(
        select(WorkingBoardInsights).where(
            WorkingBoardInsights.working_board_id == board_id
        )
    )
    if not insight:
        return None

    mentor = db.get(User, insight.mentor_user_id)
    mentor_name = f"{mentor.first_name} {mentor.last_name}".strip() if mentor else None

    rev_count = db.scalar(
        select(func.count(WorkingBoardInsightRevision.id)).where(
            WorkingBoardInsightRevision.insight_id == insight.id
        )
    ) or 1

    return MentorWorkingBoardInsightsDetail(
        id=insight.id,
        working_board_id=insight.working_board_id,
        mentor_user_id=insight.mentor_user_id,
        mentor_name=mentor_name,
        insights_text=insight.insights_text,
        publication_status=insight.publication_status,
        published_at=insight.published_at,
        created_at=insight.created_at,
        updated_at=insight.updated_at,
        revision_count=rev_count,
    )


def upsert_mentor_working_board_insights(
    db: Session,
    current_user: User,
    board_id: UUID,
    data: MentorWorkingBoardInsightsUpsertRequest,
) -> MentorWorkingBoardInsightsDetail:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    _require_mentor_for_team(db, current_user.id, board.team_id)

    insight = db.scalar(
        select(WorkingBoardInsights).where(
            WorkingBoardInsights.working_board_id == board_id
        )
    )

    now = datetime.now(timezone.utc)
    was_published = False

    if insight:
        was_published = (insight.publication_status == "published")
        insight.insights_text = data.insights_text
        insight.publication_status = data.publication_status
        insight.mentor_user_id = current_user.id
        if data.publication_status == "published" and not insight.published_at:
            insight.published_at = now
    else:
        insight = WorkingBoardInsights(
            working_board_id=board.id,
            mentor_user_id=current_user.id,
            insights_text=data.insights_text,
            publication_status=data.publication_status,
            published_at=now if data.publication_status == "published" else None,
        )
        db.add(insight)

    db.flush()

    # Maintain revision history / audit records
    rev_count = db.scalar(
        select(func.count(WorkingBoardInsightRevision.id)).where(
            WorkingBoardInsightRevision.insight_id == insight.id
        )
    ) or 0

    revision = WorkingBoardInsightRevision(
        insight_id=insight.id,
        working_board_id=board.id,
        mentor_user_id=current_user.id,
        insights_text=insight.insights_text,
        publication_status=insight.publication_status,
        revision_number=rev_count + 1,
    )
    db.add(revision)

    db.commit()
    db.refresh(insight)

    # Trigger notification if transitioning from unpublished to published
    if data.publication_status == "published" and not was_published:
        session_obj = db.get(DBSession, board.session_id)
        session_title = session_obj.title if session_obj else None
        send_working_board_insights_notification(
            db=db,
            team_id=board.team_id,
            mentor=current_user,
            session_title=session_title,
        )

    mentor_name = f"{current_user.first_name} {current_user.last_name}".strip()
    return MentorWorkingBoardInsightsDetail(
        id=insight.id,
        working_board_id=insight.working_board_id,
        mentor_user_id=insight.mentor_user_id,
        mentor_name=mentor_name,
        insights_text=insight.insights_text,
        publication_status=insight.publication_status,
        published_at=insight.published_at,
        created_at=insight.created_at,
        updated_at=insight.updated_at,
        revision_count=rev_count + 1,
    )


def publish_mentor_working_board_insights(
    db: Session,
    current_user: User,
    board_id: UUID,
) -> MentorWorkingBoardInsightsDetail:
    board = db.get(WorkingBoard, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working Board not found.",
        )

    _require_mentor_for_team(db, current_user.id, board.team_id)

    insight = db.scalar(
        select(WorkingBoardInsights).where(
            WorkingBoardInsights.working_board_id == board_id
        )
    )
    if not insight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No insights found to publish. Please save a draft first.",
        )

    was_published = (insight.publication_status == "published")
    now = datetime.now(timezone.utc)

    insight.publication_status = "published"
    if not insight.published_at:
        insight.published_at = now
    insight.mentor_user_id = current_user.id

    db.flush()

    # Record revision
    rev_count = db.scalar(
        select(func.count(WorkingBoardInsightRevision.id)).where(
            WorkingBoardInsightRevision.insight_id == insight.id
        )
    ) or 0

    revision = WorkingBoardInsightRevision(
        insight_id=insight.id,
        working_board_id=board.id,
        mentor_user_id=current_user.id,
        insights_text=insight.insights_text,
        publication_status="published",
        revision_number=rev_count + 1,
    )
    db.add(revision)

    db.commit()
    db.refresh(insight)

    # Avoid duplicate notifications caused by retrying the same publish request
    if not was_published:
        session_obj = db.get(DBSession, board.session_id)
        session_title = session_obj.title if session_obj else None
        send_working_board_insights_notification(
            db=db,
            team_id=board.team_id,
            mentor=current_user,
            session_title=session_title,
        )

    mentor = db.get(User, insight.mentor_user_id)
    mentor_name = f"{mentor.first_name} {mentor.last_name}".strip() if mentor else None

    return MentorWorkingBoardInsightsDetail(
        id=insight.id,
        working_board_id=insight.working_board_id,
        mentor_user_id=insight.mentor_user_id,
        mentor_name=mentor_name,
        insights_text=insight.insights_text,
        publication_status=insight.publication_status,
        published_at=insight.published_at,
        created_at=insight.created_at,
        updated_at=insight.updated_at,
        revision_count=rev_count + 1,
    )
