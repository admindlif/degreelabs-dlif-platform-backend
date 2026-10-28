"""
Mentor checklist service.

Handles:
- Mentor reviewing individual fellow checklist responses (accept/reject)
- Checklist feedback message threads (fellow ↔ mentor)
- Mentor assignment queries
- Email notifications triggered by mentor actions
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session as DBSession

from app.models.checklist import (
    ChecklistFeedbackMessage,
    ChecklistItem,
    ChecklistReviewEvent,
    ChecklistReviewStatus,
    FellowChecklistCompletion,
    MentorTeamAssignment,
)
from app.models.submission import TeamSubmission
from app.models.working_board import WorkingBoard
from app.models.team import Team, TeamMembership
from app.models.user import User, UserRole
from app.schemas.mentor import (
    ChecklistFeedbackMessageResponse,
    ChecklistFeedbackThreadResponse,
    MentorAssignedTeamSummary,
    MentorAssignedTeamsResponse,
    MentorFellowChecklistSummary,
    MentorTeamChecklistResponse,
)
from app.services.email import send_email
from app.services.checklist import (
    checklist_visibility_conditions,
    get_team_checklist_scope,
)
from app.services.notifications import create_portal_notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _get_assignment(db: DBSession, mentor_id: UUID, team_id: UUID) -> MentorTeamAssignment | None:
    return db.scalar(
        select(MentorTeamAssignment).where(
            MentorTeamAssignment.mentor_user_id == mentor_id,
            MentorTeamAssignment.team_id == team_id,
        )
    )


def _require_mentor_assigned_to_team(db: DBSession, mentor_id: UUID, team_id: UUID) -> None:
    if not _get_assignment(db, mentor_id, team_id):
        raise _forbidden("You are not assigned to this team.")


def _require_fellow_in_team(
    db: DBSession,
    team_id: UUID,
    fellow_user_id: UUID,
) -> TeamMembership:
    membership = db.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == fellow_user_id,
        )
    )
    if not membership:
        raise _forbidden("This fellow is not a member of the specified team.")
    return membership


def _require_checklist_item_visible_to_team(
    db: DBSession,
    team_id: UUID,
    checklist_item_id: UUID,
) -> ChecklistItem:
    team, phase_id = get_team_checklist_scope(db, team_id)
    item = db.scalar(
        select(ChecklistItem).where(
            ChecklistItem.id == checklist_item_id,
            *checklist_visibility_conditions(team.cohort_id, phase_id, team.id),
        )
    )
    if not item:
        raise _not_found("Checklist item not found for this team scope.")
    return item


# ---------------------------------------------------------------------------
# Mentor – assigned teams
# ---------------------------------------------------------------------------


def get_mentor_assigned_teams(
    db: DBSession,
    mentor: User,
) -> MentorAssignedTeamsResponse:
    rows = db.scalars(
        select(MentorTeamAssignment)
        .where(MentorTeamAssignment.mentor_user_id == mentor.id)
    ).all()
    teams = []
    for row in rows:
        team = db.get(Team, row.team_id)
        if not team or not team.is_active:
            continue
        cohort = team.cohort
        teams.append(
            MentorAssignedTeamSummary(
                team_id=team.id,
                team_name=team.name,
                cohort_id=cohort.id,
                cohort_name=cohort.name,
            )
        )
    return MentorAssignedTeamsResponse(teams=teams)


# ---------------------------------------------------------------------------
# Mentor – view fellow checklist responses for a team
# ---------------------------------------------------------------------------


def get_team_checklist_for_mentor(
    db: DBSession,
    mentor: User,
    team_id: UUID,
) -> MentorTeamChecklistResponse:
    _require_mentor_assigned_to_team(db, mentor.id, team_id)

    team, phase_id = get_team_checklist_scope(db, team_id)

    # All team members
    memberships = db.scalars(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).all()

    # All checklist items visible to this cohort (active items)
    items = db.scalars(
        select(ChecklistItem)
        .where(*checklist_visibility_conditions(team.cohort_id, phase_id, team.id))
        .order_by(ChecklistItem.sequence, ChecklistItem.created_at)
    ).all()

    rows: list[MentorFellowChecklistSummary] = []

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

            # Evidence availability is informational only. A published Working
            # Board never proves completion and a legacy TeamSubmission is not
            # required for the new collaborative workflow.
            submission_present = False
            if item.required_submission_session_id:
                wb = db.scalar(
                    select(WorkingBoard).where(
                        WorkingBoard.session_id == item.required_submission_session_id,
                        WorkingBoard.team_id == team_id,
                        WorkingBoard.is_published.is_(True),
                    )
                )
                if wb is not None:
                    submission_present = True
                else:
                    # Historical cohorts may still use TeamSubmission. Preserve
                    # that evidence without making it a requirement.
                    sub = db.scalar(
                        select(TeamSubmission).where(
                            TeamSubmission.session_id == item.required_submission_session_id,
                            TeamSubmission.team_id == team_id,
                        )
                    )
                    submission_present = sub is not None
            else:
                # Not document-gated; mark as N/A → True so frontend doesn't warn
                submission_present = True

            rows.append(
                MentorFellowChecklistSummary(
                    fellow_user_id=fellow.id,
                    fellow_name=fellow_name,
                    checklist_item_id=item.id,
                    checklist_item_title=item.title,
                    review_status=(
                        completion.review_status.value
                        if completion
                        else "not_checked"
                    ),
                    submission_present=submission_present,
                )
            )

    return MentorTeamChecklistResponse(
        team_id=team.id,
        team_name=team.name,
        items=rows,
    )


# ---------------------------------------------------------------------------
# Mentor – get individual fellow's checklist response detail
# ---------------------------------------------------------------------------


def get_fellow_completion_for_mentor(
    db: DBSession,
    mentor: User,
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
) -> FellowChecklistCompletion | None:
    _require_mentor_assigned_to_team(db, mentor.id, team_id)

    _require_fellow_in_team(db, team_id, fellow_user_id)
    _require_checklist_item_visible_to_team(db, team_id, checklist_item_id)

    return db.scalar(
        select(FellowChecklistCompletion).where(
            FellowChecklistCompletion.checklist_item_id == checklist_item_id,
            FellowChecklistCompletion.user_id == fellow_user_id,
        )
    )


# ---------------------------------------------------------------------------
# Mentor – accept / reject a fellow's checklist response
# ---------------------------------------------------------------------------


def mentor_review_checklist_item(
    db: DBSession,
    mentor: User,
    team_id: UUID,
    checklist_item_id: UUID,
    fellow_user_id: UUID,
    action: str,  # "accept" | "reject"
    rejection_reason: str | None,
) -> FellowChecklistCompletion:
    _require_mentor_assigned_to_team(db, mentor.id, team_id)

    _require_fellow_in_team(db, team_id, fellow_user_id)
    item = _require_checklist_item_visible_to_team(db, team_id, checklist_item_id)

    completion = db.scalar(
        select(FellowChecklistCompletion).where(
            FellowChecklistCompletion.checklist_item_id == checklist_item_id,
            FellowChecklistCompletion.user_id == fellow_user_id,
        )
    )

    if not completion or completion.review_status == ChecklistReviewStatus.NOT_CHECKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Fellow has not submitted this checklist item yet.",
        )

    now = datetime.now(timezone.utc)
    if action == "accept":
        completion.review_status = ChecklistReviewStatus.ACCEPTED
        completion.rejection_reason = None
    elif action == "reject":
        if not rejection_reason or not rejection_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="rejection_reason is required when action is 'reject'.",
            )
        completion.review_status = ChecklistReviewStatus.REJECTED
        completion.rejection_reason = rejection_reason.strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action must be 'accept' or 'reject'.",
        )

    completion.reviewed_by_mentor_id = mentor.id
    completion.reviewed_at = now

    db.add(
        ChecklistReviewEvent(
            completion_id=completion.id,
            checklist_item_id=checklist_item_id,
            fellow_user_id=fellow_user_id,
            mentor_user_id=mentor.id,
            decision=completion.review_status,
            rejection_reason=completion.rejection_reason,
        )
    )

    create_portal_notification(
        db,
        recipient_user_id=fellow_user_id,
        notification_type="checklist_reviewed",
        title=f"Checklist item {completion.review_status.value}",
        message=f"Your response for '{item.title}' was {completion.review_status.value}.",
        target_url=f"/checklist-feedback?itemId={item.id}",
        metadata={
            "team_id": str(team_id),
            "checklist_item_id": str(item.id),
            "review_status": completion.review_status.value,
        },
    )

    db.commit()
    db.refresh(completion)

    # Send email notification to fellow
    fellow = db.get(User, fellow_user_id)
    if fellow:
        try:
            _send_mentor_review_notification(fellow=fellow, item=item, action=action)
        except Exception:
            logger.exception(
                "Failed to send checklist review notification to fellow %s",
                fellow_user_id,
            )

    return completion


# ---------------------------------------------------------------------------
# Checklist Feedback Messages
# ---------------------------------------------------------------------------


def get_feedback_thread(
    db: DBSession,
    item_id: UUID,
    fellow_user_id: UUID,
) -> ChecklistFeedbackThreadResponse:
    messages = db.scalars(
        select(ChecklistFeedbackMessage)
        .where(
            ChecklistFeedbackMessage.checklist_item_id == item_id,
            ChecklistFeedbackMessage.fellow_user_id == fellow_user_id,
        )
        .order_by(ChecklistFeedbackMessage.created_at)
    ).all()

    return ChecklistFeedbackThreadResponse(
        checklist_item_id=item_id,
        fellow_user_id=fellow_user_id,
        messages=[_feedback_message_response(db, message) for message in messages],
    )


def _feedback_message_response(
    db: DBSession,
    message: ChecklistFeedbackMessage,
) -> ChecklistFeedbackMessageResponse:
    author = db.get(User, message.author_user_id)
    author_name = (
        f"{author.first_name} {author.last_name}".strip() if author else None
    )
    return ChecklistFeedbackMessageResponse(
        id=message.id,
        checklist_item_id=message.checklist_item_id,
        fellow_user_id=message.fellow_user_id,
        author_user_id=message.author_user_id,
        author_role=message.author_role,
        author_name=author_name,
        message=message.message,
        created_at=message.created_at,
    )


def post_feedback_message(
    db: DBSession,
    item_id: UUID,
    fellow_user_id: UUID,
    author: User,
    message: str,
    team_id: UUID | None = None,
    notify_recipient: bool = True,
) -> ChecklistFeedbackMessageResponse:
    if author.role in (UserRole.MENTOR, UserRole.PROGRAM_MANAGER):
        if team_id is None:
            raise _forbidden("A team is required for mentor checklist feedback.")
        _require_mentor_assigned_to_team(db, author.id, team_id)
        _require_fellow_in_team(db, team_id, fellow_user_id)
        item = _require_checklist_item_visible_to_team(db, team_id, item_id)
    else:
        if author.id != fellow_user_id:
            raise _forbidden("Fellows can only post to their own checklist thread.")
        from app.services.checklist import _fellow_scope

        cohort_id, phase_id, fellow_team_id = _fellow_scope(db, author)
        item = db.scalar(
            select(ChecklistItem).where(
                ChecklistItem.id == item_id,
                *checklist_visibility_conditions(cohort_id, phase_id, fellow_team_id),
            )
        )
        if not item:
            raise _not_found("Checklist item not found.")

    msg = ChecklistFeedbackMessage(
        checklist_item_id=item_id,
        fellow_user_id=fellow_user_id,
        author_user_id=author.id,
        author_role=author.role.value,
        message=message.strip(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)

    if author.role in (UserRole.MENTOR, UserRole.PROGRAM_MANAGER):
        create_portal_notification(
            db,
            recipient_user_id=fellow_user_id,
            notification_type="checklist_discussion",
            title=f"New mentor comment on {item.title}",
            message="Your mentor posted a checklist feedback message.",
            target_url=f"/checklist-feedback?itemId={item.id}",
            metadata={"checklist_item_id": str(item.id)},
        )
    else:
        membership = db.scalar(
            select(TeamMembership).where(
                TeamMembership.user_id == fellow_user_id,
                TeamMembership.cohort_id == item.cohort_id,
            )
        )
        if membership is None:
            # Global items have no item.cohort_id, so use the fellow's active membership.
            membership = db.scalar(
                select(TeamMembership).where(TeamMembership.user_id == fellow_user_id)
            )
        if membership:
            assignments = db.scalars(
                select(MentorTeamAssignment).where(
                    MentorTeamAssignment.team_id == membership.team_id
                )
            ).all()
            fellow_name = f"{author.first_name} {author.last_name}".strip()
            for assignment in assignments:
                create_portal_notification(
                    db,
                    recipient_user_id=assignment.mentor_user_id,
                    notification_type="checklist_discussion",
                    title=f"New comment from {fellow_name or 'Fellow'}",
                    message=f"Discussion on '{item.title}'.",
                    target_url=(
                        f"/checklist-feedback?teamId={membership.team_id}"
                        f"&itemId={item.id}&fellowId={fellow_user_id}"
                    ),
                    metadata={
                        "team_id": str(membership.team_id),
                        "checklist_item_id": str(item.id),
                        "fellow_id": str(fellow_user_id),
                    },
                )
    db.commit()
    db.refresh(msg)

    if notify_recipient:
        try:
            _send_checklist_feedback_notification(
                db=db,
                item=item,
                author=author,
                fellow_user_id=fellow_user_id,
            )
        except Exception:
            logger.exception(
                "Failed to send checklist feedback notification (item=%s, author=%s)",
                item_id,
                author.id,
            )

    return _feedback_message_response(db, msg)


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------


def _send_mentor_review_notification(
    fellow: User,
    item: ChecklistItem,
    action: str,
) -> None:
    verb = "accepted" if action == "accept" else "rejected"
    subject = f"DLIF Checklist Item {verb.capitalize()}: {item.title}"
    text_body = (
        f"Hello {fellow.first_name},\n\n"
        f"Your checklist item \"{item.title}\" has been {verb} by your mentor.\n\n"
        f"Log in to your DLIF Fellow Portal to view the feedback.\n\n"
        f"— DegreeLabs DLIF Team"
    )
    html_body = f"""
    <p>Hello {fellow.first_name},</p>
    <p>Your checklist item <strong>{item.title}</strong> has been <strong>{verb}</strong> by your mentor.</p>
    <p>Log in to your DLIF Fellow Portal to view the feedback.</p>
    <p>— DegreeLabs DLIF Team</p>
    """
    send_email(
        to_address=fellow.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def _send_checklist_feedback_notification(
    db: DBSession,
    item: ChecklistItem,
    author: User,
    fellow_user_id: UUID,
) -> None:
    from app.models.user import UserRole

    if author.role in (UserRole.MENTOR, UserRole.PROGRAM_MANAGER):
        # Notify the fellow
        recipient = db.get(User, fellow_user_id)
        if not recipient:
            return
        subject = f"DLIF Checklist Feedback: {item.title}"
        text_body = (
            f"Hello {recipient.first_name},\n\n"
            f"Your mentor has posted a comment on the checklist item \"{item.title}\".\n\n"
            f"Log in to your DLIF Fellow Portal to view the message.\n\n"
            f"— DegreeLabs DLIF Team"
        )
        html_body = f"""
        <p>Hello {recipient.first_name},</p>
        <p>Your mentor has posted a comment on the checklist item <strong>{item.title}</strong>.</p>
        <p>Log in to your DLIF Fellow Portal to view the message.</p>
        <p>— DegreeLabs DLIF Team</p>
        """
    else:
        # Fellow posted; notify all assigned mentors for the team
        # Look up which teams the fellow belongs to and find mentors assigned to those teams
        fellowships = db.scalars(
            select(TeamMembership).where(TeamMembership.user_id == fellow_user_id)
        ).all()
        if not fellowships:
            return

        notified: set[UUID] = set()
        for tm in fellowships:
            assignments = db.scalars(
                select(MentorTeamAssignment).where(
                    MentorTeamAssignment.team_id == tm.team_id
                )
            ).all()
            for assignment in assignments:
                if assignment.mentor_user_id in notified:
                    continue
                mentor = db.get(User, assignment.mentor_user_id)
                if not mentor:
                    continue
                notified.add(mentor.id)
                fellow_obj = db.get(User, fellow_user_id)
                fellow_name = f"{fellow_obj.first_name} {fellow_obj.last_name}".strip() if fellow_obj else "A fellow"
                subject = f"DLIF Checklist Feedback from {fellow_name}: {item.title}"
                text_body = (
                    f"Hello {mentor.first_name},\n\n"
                    f"{fellow_name} has posted a comment on the checklist item \"{item.title}\".\n\n"
                    f"Log in to your DLIF Mentor Portal to view and respond.\n\n"
                    f"— DegreeLabs DLIF Team"
                )
                html_body = f"""
                <p>Hello {mentor.first_name},</p>
                <p><strong>{fellow_name}</strong> has posted a comment on the checklist item
                <strong>{item.title}</strong>.</p>
                <p>Log in to your DLIF Mentor Portal to view and respond.</p>
                <p>— DegreeLabs DLIF Team</p>
                """
                send_email(
                    to_address=mentor.email,
                    subject=subject,
                    html_body=html_body,
                    text_body=text_body,
                )
        return


def send_submission_notification_to_mentors(
    db: DBSession,
    team_id: UUID,
    session_id: UUID,
    submitted_by: User,
) -> None:
    """
    Trigger 1: Notify assigned mentors when a Team Lead submits a document.
    Email body contains only: batch number (cohort), team name, week/session,
    and checklist item titles — NO document content.
    """
    from app.models.session import Session as DBSessionModel
    from app.models.cohort import Cohort

    team = db.get(Team, team_id)
    if not team:
        return

    session = db.get(DBSessionModel, session_id)
    cohort = db.get(Cohort, team.cohort_id) if team else None

    # Collect checklist item titles for this session
    items = db.scalars(
        select(ChecklistItem).where(
            ChecklistItem.session_id == session_id,
            ChecklistItem.is_active.is_(True),
        ).order_by(ChecklistItem.sequence)
    ).all()

    item_titles = "\n".join(f"  • {i.title}" for i in items) if items else "  (no checklist items)"

    # Find all mentors assigned to this team
    assignments = db.scalars(
        select(MentorTeamAssignment).where(MentorTeamAssignment.team_id == team_id)
    ).all()

    notified: set[UUID] = set()
    for assignment in assignments:
        if assignment.mentor_user_id in notified:
            continue
        mentor = db.get(User, assignment.mentor_user_id)
        if not mentor:
            continue
        notified.add(mentor.id)

        create_portal_notification(
            db,
            recipient_user_id=mentor.id,
            notification_type="submission_updated",
            title=f"Document submitted by {team.name}",
            message=f"A submission for {session.title if session else 'a session'} is ready for review.",
            target_url=f"/submissions?teamId={team_id}",
            metadata={
                "team_id": str(team_id),
                "session_id": str(session_id),
                "submitted_by_user_id": str(submitted_by.id),
            },
        )

        subject = f"DLIF Submission: {team.name} — {cohort.name if cohort else 'Cohort'}"
        week_info = f"Week {session.week.week_number}" if session and session.week else "Week —"
        session_info = f"Session {session.session_number}" if session else "Session —"

        text_body = (
            f"Hello {mentor.first_name},\n\n"
            f"A document has been submitted for your review.\n\n"
            f"Batch/Cohort: {cohort.name if cohort else '—'}\n"
            f"Team: {team.name}\n"
            f"{week_info} / {session_info}\n\n"
            f"Checklist items for this session:\n{item_titles}\n\n"
            f"Log in to your DLIF Mentor Portal to review.\n\n"
            f"— DegreeLabs DLIF Team"
        )
        html_body = f"""
        <p>Hello {mentor.first_name},</p>
        <p>A document has been submitted for your review.</p>
        <ul>
            <li><strong>Batch/Cohort:</strong> {cohort.name if cohort else '—'}</li>
            <li><strong>Team:</strong> {team.name}</li>
            <li><strong>Week / Session:</strong> {week_info} / {session_info}</li>
        </ul>
        <p><strong>Checklist items for this session:</strong></p>
        <ul>{''.join(f'<li>{i.title}</li>' for i in items) if items else '<li>None</li>'}</ul>
        <p>Log in to your DLIF Mentor Portal to review.</p>
        <p>— DegreeLabs DLIF Team</p>
        """
        try:
            send_email(
                to_address=mentor.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except Exception:
            logger.exception(
                "Failed to send submission notification to mentor %s for team %s",
                mentor.id,
                team_id,
            )

    db.commit()


def send_submission_insight_notification(
    db: DBSession,
    team_id: UUID,
    reviewer: User,
) -> None:
    """
    Trigger 3: Notify all team members when a mentor posts Submission Insights.
    """
    memberships = db.scalars(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).all()

    team = db.get(Team, team_id)
    team_name = team.name if team else "your team"

    notified: set[UUID] = set()
    for tm in memberships:
        if tm.user_id in notified:
            continue
        notified.add(tm.user_id)
        fellow = db.get(User, tm.user_id)
        if not fellow:
            continue

        subject = f"DLIF Submission Insights: {team_name}"
        text_body = (
            f"Hello {fellow.first_name},\n\n"
            f"Your mentor has posted feedback on your team's document submission.\n\n"
            f"Log in to your DLIF Fellow Portal to view.\n\n"
            f"— DegreeLabs DLIF Team"
        )
        html_body = f"""
        <p>Hello {fellow.first_name},</p>
        <p>Your mentor has posted feedback on your team's document submission for
        <strong>{team_name}</strong>.</p>
        <p>Log in to your DLIF Fellow Portal to view.</p>
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
                "Failed to send submission insight notification to fellow %s",
                fellow.id,
            )
