from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.checklist import (
    ChecklistItem,
    ChecklistReviewStatus,
    FellowChecklistCompletion,
    MentorTeamAssignment,
)
from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.progression import TeamPhaseProgression
from app.models.session import Session as DBSession
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.models.week import Week
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.checklist import (
    ChecklistActivationUpdate,
    ChecklistItemCreate,
    ChecklistItemUpdate,
    ChecklistReminderItem,
    ChecklistRemindersResponse,
    ChecklistSummaryResponse,
    FellowChecklistItemResponse,
    FellowChecklistResponse,
    MentorChecklistItemCreate,
    MentorChecklistItemResponse,
    MentorChecklistItemUpdate,
)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _fellow_scope(
    db: Session,
    current_user: User,
) -> tuple[UUID, UUID | None, UUID | None]:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise _not_found("No active enrollment found for this Fellow.")

    membership = db.scalar(
        select(TeamMembership).where(
            TeamMembership.user_id == current_user.id,
            TeamMembership.cohort_id == enrollment.cohort_id,
        )
    )
    phase_id = get_current_checklist_phase_id(
        db,
        cohort_id=enrollment.cohort_id,
        team_id=membership.team_id if membership else None,
    )
    return enrollment.cohort_id, phase_id, membership.team_id if membership else None


def require_visible_fellow_checklist_item(
    db: Session,
    current_user: User,
    item_id: UUID,
) -> tuple[ChecklistItem, UUID, UUID | None]:
    cohort_id, phase_id, team_id = _fellow_scope(db, current_user)
    item = db.scalar(
        select(ChecklistItem).where(
            ChecklistItem.id == item_id,
            *checklist_visibility_conditions(cohort_id, phase_id, team_id),
        )
    )
    if not item:
        raise _not_found("Checklist item not found.")
    return item, cohort_id, phase_id


def get_current_checklist_phase_id(
    db: Session,
    *,
    cohort_id: UUID,
    team_id: UUID | None,
) -> UUID | None:
    """Return the highest active phase the team is approved to enter."""
    cohort = db.get(Cohort, cohort_id)
    if not cohort:
        return None

    phases = list(
        db.scalars(
            select(Phase)
            .where(
                Phase.program_id == cohort.program_id,
                Phase.is_active.is_(True),
            )
            .order_by(Phase.sequence.asc())
        ).all()
    )
    if not phases:
        return None

    accessible_codes = {phases[0].code.upper()}
    if team_id:
        approved_codes = db.scalars(
            select(TeamPhaseProgression.phase_code).where(
                TeamPhaseProgression.team_id == team_id,
                TeamPhaseProgression.status == "approved",
            )
        ).all()
        accessible_codes.update(code.upper() for code in approved_codes)

    accessible_phases = [
        phase for phase in phases if phase.code.upper() in accessible_codes
    ]
    return max(accessible_phases, key=lambda phase: phase.sequence).id


def get_team_checklist_scope(
    db: Session,
    team_id: UUID,
) -> tuple[Team, UUID | None]:
    team = db.get(Team, team_id)
    if not team:
        raise _not_found("Team not found.")
    return team, get_current_checklist_phase_id(
        db,
        cohort_id=team.cohort_id,
        team_id=team.id,
    )


def checklist_visibility_conditions(
    cohort_id: UUID,
    phase_id: UUID | None,
    team_id: UUID | None = None,
):
    conditions = [
        ChecklistItem.is_active.is_(True),
        or_(
            ChecklistItem.cohort_id.is_(None),
            ChecklistItem.cohort_id == cohort_id,
        ),
        (
            or_(ChecklistItem.team_id.is_(None), ChecklistItem.team_id == team_id)
            if team_id is not None
            else ChecklistItem.team_id.is_(None)
        ),
    ]

    if phase_id is None:
        conditions.extend(
            [
                ChecklistItem.phase_id.is_(None),
                ChecklistItem.week_id.is_(None),
                # Allow session_number-scoped items even when no phase is active
                or_(
                    ChecklistItem.session_id.is_(None),
                    # session_number items have session_id=NULL; include them
                    ChecklistItem.session_number.isnot(None),
                ),
            ]
        )
    else:
        week_ids = select(Week.id).where(Week.phase_id == phase_id)
        session_ids = select(DBSession.id).where(
            DBSession.cohort_id == cohort_id,
            DBSession.phase_id == phase_id,
        )
        # Session numbers for sessions in this cohort+phase – used to resolve
        # global session_number-scoped checklist items.
        session_numbers_in_scope = select(DBSession.session_number).where(
            DBSession.cohort_id == cohort_id,
            DBSession.phase_id == phase_id,
        )
        conditions.extend(
            [
                or_(
                    ChecklistItem.phase_id.is_(None),
                    ChecklistItem.phase_id == phase_id,
                ),
                or_(
                    ChecklistItem.week_id.is_(None),
                    ChecklistItem.week_id.in_(week_ids),
                ),
                or_(
                    ChecklistItem.session_id.is_(None),
                    ChecklistItem.session_id.in_(session_ids),
                    # Global session-number-scoped items (e.g. Session 9 constants)
                    ChecklistItem.session_number.in_(session_numbers_in_scope),
                ),
            ]
        )

    return conditions


def _status_for(
    item: ChecklistItem,
    completion: FellowChecklistCompletion | None,
    now: datetime,
) -> str:
    if (
        completion
        and completion.is_completed
        and completion.review_status
        in (ChecklistReviewStatus.NOT_CHECKED, ChecklistReviewStatus.ACCEPTED)
    ):
        return "completed"
    if item.due_at and item.due_at < now:
        return "overdue"
    return "pending"


def _fellow_item_response(
    db: Session,
    item: ChecklistItem,
    completion: FellowChecklistCompletion | None,
    now: datetime,
    cohort_id: UUID,
) -> FellowChecklistItemResponse:
    is_submitted = bool(completion and completion.is_completed)
    # NOT_CHECKED is retained for legacy checkbox-only checklist records. New
    # written responses are moved to PENDING_REVIEW below and only count again
    # after mentor acceptance.
    is_completed = bool(
        is_submitted
        and completion.review_status
        in (ChecklistReviewStatus.NOT_CHECKED, ChecklistReviewStatus.ACCEPTED)
    )
    session = db.get(DBSession, item.session_id) if item.session_id else None
    week_id = item.week_id or (session.week_id if session else None)
    week = db.get(Week, week_id) if week_id else None
    cohort = db.get(Cohort, item.cohort_id or cohort_id)
    return FellowChecklistItemResponse(
        id=item.id,
        title=item.title,
        description=item.description,
        category=item.category,
        due_at=item.due_at,
        status=_status_for(item, completion, now),
        is_required=item.is_required,
        is_submitted=is_submitted,
        is_completed=is_completed,
        completed_at=completion.completed_at if is_submitted else None,
        contribution=completion.contribution if completion else None,
        reflection=completion.reflection if completion else None,
        my_thoughts=completion.my_thoughts if completion else None,
        review_status=completion.review_status.value if completion else "not_checked",
        rejection_reason=completion.rejection_reason if completion else None,
        reviewed_at=completion.reviewed_at if completion else None,
        session_id=item.session_id,
        session_name=session.title if session else None,
        session_number=item.session_number,  # None for non-global items
        week_number=week.week_number if week else None,
        cohort_name=cohort.name if cohort else None,
        action_label=item.action_label,
        action_url=item.action_url,
        sequence=item.sequence,
    )


def get_fellow_checklist(
    db: Session,
    current_user: User,
) -> FellowChecklistResponse:
    cohort_id, phase_id, team_id = _fellow_scope(db, current_user)
    rows = db.execute(
        select(ChecklistItem, FellowChecklistCompletion)
        .outerjoin(
            FellowChecklistCompletion,
            (FellowChecklistCompletion.checklist_item_id == ChecklistItem.id)
            & (FellowChecklistCompletion.user_id == current_user.id),
        )
        .where(*checklist_visibility_conditions(cohort_id, phase_id, team_id))
        .order_by(
            ChecklistItem.sequence,
            ChecklistItem.due_at.asc().nulls_last(),
            ChecklistItem.created_at,
        )
    ).all()

    now = datetime.now(timezone.utc)
    items = [
        _fellow_item_response(db, item, completion, now, cohort_id)
        for item, completion in rows
    ]
    total = len(items)
    completed = sum(item.is_completed for item in items)
    overdue = sum(item.status == "overdue" for item in items)
    pending = total - completed
    percentage = (completed * 100 + total // 2) // total if total else 0

    return FellowChecklistResponse(
        summary=ChecklistSummaryResponse(
            total=total,
            completed=completed,
            pending=pending,
            overdue=overdue,
            percentage=percentage,
        ),
        items=items,
    )


def get_fellow_checklist_reminders(
    db: Session,
    current_user: User,
) -> ChecklistRemindersResponse:
    cohort_id, phase_id, team_id = _fellow_scope(db, current_user)
    rows = db.execute(
        select(ChecklistItem, FellowChecklistCompletion)
        .outerjoin(
            FellowChecklistCompletion,
            (FellowChecklistCompletion.checklist_item_id == ChecklistItem.id)
            & (FellowChecklistCompletion.user_id == current_user.id),
        )
        .where(*checklist_visibility_conditions(cohort_id, phase_id, team_id))
        .order_by(
            ChecklistItem.sequence,
            ChecklistItem.due_at.asc().nulls_last(),
            ChecklistItem.created_at,
        )
    ).all()

    now = datetime.now(timezone.utc)
    upcoming_limit = now + timedelta(hours=48)

    eligible_reminders: list[tuple[int, datetime, int, ChecklistReminderItem]] = []

    for item, completion in rows:
        status_val = _status_for(item, completion, now)

        # Exclude completed items
        if status_val == "completed":
            continue

        # A submitted response is waiting on the mentor, not the fellow.
        if completion and completion.review_status == ChecklistReviewStatus.PENDING_REVIEW:
            continue

        # Undated items are excluded from reminders
        if item.due_at is None:
            continue

        due_at = (
            item.due_at
            if item.due_at.tzinfo is not None
            else item.due_at.replace(tzinfo=timezone.utc)
        )

        if status_val == "overdue":
            message = "This checklist item is overdue."
            sort_priority = 0
        elif status_val == "pending" and due_at <= upcoming_limit:
            message = "This checklist item is due soon."
            sort_priority = 1
        else:
            continue

        reminder_item = ChecklistReminderItem(
            id=item.id,
            title=item.title,
            message=message,
            status=status_val,
            due_at=due_at,
            target_url="/notifications",
        )
        eligible_reminders.append(
            (sort_priority, due_at, item.sequence, reminder_item)
        )

    # Order overdue reminders first, then upcoming reminders by deadline
    eligible_reminders.sort(key=lambda entry: (entry[0], entry[1], entry[2]))

    total_count = len(eligible_reminders)
    reminders = [entry[3] for entry in eligible_reminders[:5]]

    return ChecklistRemindersResponse(
        total_count=total_count,
        reminders=reminders,
    )


def update_fellow_checklist_completion(
    db: Session,
    current_user: User,
    item_id: UUID,
    is_completed: bool,
    contribution: str | None = None,
    reflection: str | None = None,
    my_thoughts: str | None = None,
) -> FellowChecklistItemResponse:
    item, cohort_id, phase_id = require_visible_fellow_checklist_item(
        db, current_user, item_id
    )

    completion = db.scalar(
        select(FellowChecklistCompletion).where(
            FellowChecklistCompletion.checklist_item_id == item.id,
            FellowChecklistCompletion.user_id == current_user.id,
        )
    )
    now = datetime.now(timezone.utc)
    if not completion:
        completion = FellowChecklistCompletion(
            checklist_item_id=item.id,
            user_id=current_user.id,
            review_status=ChecklistReviewStatus.NOT_CHECKED,
        )
        db.add(completion)

    completion.is_completed = is_completed
    completion.completed_at = now if is_completed else None

    response_changed = (
        contribution is not None or reflection is not None or my_thoughts is not None
    )

    # Update written responses if provided
    if contribution is not None:
        completion.contribution = contribution
    if reflection is not None:
        completion.reflection = reflection
    if my_thoughts is not None:
        completion.my_thoughts = my_thoughts.strip() or None

    # Manage review lifecycle:
    # - Checking with contribution+reflection → pending_review
    # - Unchecking → not_checked (resets review)
    # - Re-submitting after rejection → pending_review
    if not is_completed:
        completion.review_status = ChecklistReviewStatus.NOT_CHECKED
        completion.rejection_reason = None
    elif is_completed and response_changed:
        # Any saved response, including an edit after acceptance, requires a
        # fresh mentor decision.
        completion.review_status = ChecklistReviewStatus.PENDING_REVIEW
        completion.rejection_reason = None
        completion.reviewed_by_mentor_id = None
        completion.reviewed_at = None

        membership = db.scalar(
            select(TeamMembership).where(
                TeamMembership.user_id == current_user.id,
                TeamMembership.cohort_id == cohort_id,
            )
        )
        if membership:
            from app.services.notifications import create_portal_notification

            assignments = db.scalars(
                select(MentorTeamAssignment).where(
                    MentorTeamAssignment.team_id == membership.team_id
                )
            ).all()
            fellow_name = f"{current_user.first_name} {current_user.last_name}".strip()
            for assignment in assignments:
                create_portal_notification(
                    db,
                    recipient_user_id=assignment.mentor_user_id,
                    notification_type="checklist_submitted",
                    title=f"Checklist response from {fellow_name or 'Fellow'}",
                    message=f"'{item.title}' is ready for review.",
                    target_url=(
                        f"/checklist-review?teamId={membership.team_id}"
                        f"&itemId={item.id}&fellowId={current_user.id}"
                    ),
                    metadata={
                        "team_id": str(membership.team_id),
                        "checklist_item_id": str(item.id),
                        "fellow_id": str(current_user.id),
                    },
                )

    db.commit()
    db.refresh(completion)
    return _fellow_item_response(db, item, completion, now, cohort_id)


def _validate_scope(
    db: Session,
    values: dict,
) -> None:
    team = db.get(Team, values.get("team_id")) if values.get("team_id") else None
    cohort = db.get(Cohort, values.get("cohort_id")) if values.get("cohort_id") else None
    phase = db.get(Phase, values.get("phase_id")) if values.get("phase_id") else None
    week = db.get(Week, values.get("week_id")) if values.get("week_id") else None
    session = db.get(DBSession, values.get("session_id")) if values.get("session_id") else None
    required_session = (
        db.get(DBSession, values.get("required_submission_session_id"))
        if values.get("required_submission_session_id")
        else None
    )

    if values.get("team_id") and not team:
        raise _not_found("Team not found.")
    if values.get("cohort_id") and not cohort:
        raise _not_found("Cohort not found.")
    if values.get("phase_id") and not phase:
        raise _not_found("Phase not found.")
    if values.get("week_id") and not week:
        raise _not_found("Week not found.")
    if values.get("session_id") and not session:
        raise _not_found("Session not found.")
    if values.get("required_submission_session_id") and not required_session:
        raise _not_found("Collaborative Work Session not found.")

    if team and cohort and team.cohort_id != cohort.id:
        raise HTTPException(status_code=422, detail="Team must belong to the selected Cohort.")

    if cohort and phase and cohort.program_id != phase.program_id:
        raise HTTPException(status_code=422, detail="Cohort and Phase must belong to the same Program.")
    if week and phase and week.phase_id != phase.id:
        raise HTTPException(status_code=422, detail="Week must belong to the selected Phase.")
    if session and cohort and session.cohort_id != cohort.id:
        raise HTTPException(status_code=422, detail="Session must belong to the selected Cohort.")
    if session and phase and session.phase_id != phase.id:
        raise HTTPException(status_code=422, detail="Session must belong to the selected Phase.")
    if session and week and session.week_id != week.id:
        raise HTTPException(status_code=422, detail="Session must belong to the selected Week.")
    if required_session and cohort and required_session.cohort_id != cohort.id:
        raise HTTPException(
            status_code=422,
            detail="Collaborative Work Session must belong to the selected Cohort.",
        )
    if required_session and phase and required_session.phase_id != phase.id:
        raise HTTPException(
            status_code=422,
            detail="Collaborative Work Session must belong to the selected Phase.",
        )

    effective_phase = phase
    if week:
        week_phase = db.get(Phase, week.phase_id)
        if effective_phase and week_phase and effective_phase.id != week_phase.id:
            raise HTTPException(status_code=422, detail="Week must belong to the selected Phase.")
        effective_phase = effective_phase or week_phase
    if session:
        session_phase = db.get(Phase, session.phase_id)
        if effective_phase and session_phase and effective_phase.id != session_phase.id:
            raise HTTPException(status_code=422, detail="Session must belong to the selected Phase.")
        effective_phase = effective_phase or session_phase
    if cohort and effective_phase and cohort.program_id != effective_phase.program_id:
        raise HTTPException(
            status_code=422,
            detail="Checklist scope must belong to the Cohort's Program.",
        )


def list_admin_checklist_items(db: Session) -> list[ChecklistItem]:
    return list(
        db.scalars(
            select(ChecklistItem).order_by(
                ChecklistItem.sequence,
                ChecklistItem.created_at,
            )
        ).all()
    )


def create_admin_checklist_item(
    db: Session,
    data: ChecklistItemCreate,
    created_by: User | None = None,
) -> ChecklistItem:
    values = data.model_dump()
    if values.get("team_id") and not values.get("cohort_id"):
        team = db.get(Team, values["team_id"])
        if not team:
            raise _not_found("Team not found.")
        values["cohort_id"] = team.cohort_id
    _validate_scope(db, values)
    item = ChecklistItem(
        **values,
        created_by_user_id=created_by.id if created_by else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_admin_checklist_item(
    db: Session,
    item_id: UUID,
    data: ChecklistItemUpdate,
) -> ChecklistItem:
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise _not_found("Checklist item not found.")

    if item.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System-defined checklist items cannot be modified.",
        )

    updates = data.model_dump(exclude_unset=True)
    for required_field in ("title", "is_required", "is_active", "sequence"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{required_field} cannot be null.",
            )
    resulting_values = {
        "team_id": updates.get("team_id", item.team_id),
        "cohort_id": updates.get("cohort_id", item.cohort_id),
        "phase_id": updates.get("phase_id", item.phase_id),
        "week_id": updates.get("week_id", item.week_id),
        "session_id": updates.get("session_id", item.session_id),
        "required_submission_session_id": updates.get(
            "required_submission_session_id", item.required_submission_session_id
        ),
    }
    _validate_scope(db, resulting_values)
    for field, value in updates.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


def delete_admin_checklist_item(db: Session, item_id: UUID) -> None:
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise _not_found("Checklist item not found.")

    if item.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System-defined checklist items cannot be deleted.",
        )

    completion_count = db.scalar(
        select(func.count(FellowChecklistCompletion.id)).where(
            FellowChecklistCompletion.checklist_item_id == item.id
        )
    ) or 0
    if completion_count:
        item.is_active = False
    else:
        db.delete(item)
    db.commit()


def _require_mentor_team_assignment(
    db: Session,
    mentor: User,
    team_id: UUID,
) -> Team:
    assignment = db.scalar(
        select(MentorTeamAssignment).where(
            MentorTeamAssignment.mentor_user_id == mentor.id,
            MentorTeamAssignment.team_id == team_id,
        )
    )
    team = db.get(Team, team_id)
    if not assignment or not team or not team.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this team.",
        )
    return team


def _mentor_item_response(
    db: Session,
    item: ChecklistItem,
    team: Team,
    mentor: User,
) -> MentorChecklistItemResponse:
    creator = db.get(User, item.created_by_user_id) if item.created_by_user_id else None
    creator_name = (
        f"{creator.first_name} {creator.last_name}".strip() if creator else None
    )
    return MentorChecklistItemResponse(
        id=item.id,
        team_id=item.team_id,
        cohort_id=item.cohort_id,
        phase_id=item.phase_id,
        week_id=item.week_id,
        session_id=item.session_id,
        required_submission_session_id=item.required_submission_session_id,
        title=item.title,
        description=item.description,
        category=item.category,
        due_at=item.due_at,
        action_label=item.action_label,
        action_url=item.action_url,
        is_required=item.is_required,
        is_active=item.is_active,
        sequence=item.sequence,
        created_by_user_id=item.created_by_user_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        applicable_team_id=team.id,
        applicable_team_name=team.name,
        creator_name=creator_name,
        can_edit=(item.team_id == team.id and item.created_by_user_id == mentor.id),
    )


def list_mentor_checklist_items(
    db: Session,
    mentor: User,
    team_id: UUID | None = None,
) -> list[MentorChecklistItemResponse]:
    if team_id is not None:
        teams = [_require_mentor_team_assignment(db, mentor, team_id)]
    else:
        assigned_ids = list(
            db.scalars(
                select(MentorTeamAssignment.team_id).where(
                    MentorTeamAssignment.mentor_user_id == mentor.id
                )
            ).all()
        )
        teams = [team for assigned_id in assigned_ids if (team := db.get(Team, assigned_id)) and team.is_active]

    responses: list[MentorChecklistItemResponse] = []
    for team in teams:
        phase_id = get_current_checklist_phase_id(
            db, cohort_id=team.cohort_id, team_id=team.id
        )
        items = db.scalars(
            select(ChecklistItem)
            .where(*checklist_visibility_conditions(team.cohort_id, phase_id, team.id))
            .order_by(ChecklistItem.sequence, ChecklistItem.created_at)
        ).all()
        inactive_owned = db.scalars(
            select(ChecklistItem)
            .where(
                ChecklistItem.team_id == team.id,
                ChecklistItem.created_by_user_id == mentor.id,
                ChecklistItem.is_active.is_(False),
            )
            .order_by(ChecklistItem.sequence, ChecklistItem.created_at)
        ).all()
        responses.extend(
            _mentor_item_response(db, item, team, mentor)
            for item in [*items, *inactive_owned]
        )
    return responses


def _mentor_scope_values(
    db: Session,
    team: Team,
    values: dict,
) -> dict:
    scoped = {
        **values,
        "team_id": team.id,
        "cohort_id": team.cohort_id,
    }
    current_phase_id = get_current_checklist_phase_id(
        db, cohort_id=team.cohort_id, team_id=team.id
    )
    if not current_phase_id or scoped.get("phase_id") != current_phase_id:
        raise HTTPException(
            status_code=422,
            detail="Phase must be the selected team's current applicable phase.",
        )
    _validate_scope(db, scoped)
    return scoped


def create_mentor_checklist_item(
    db: Session,
    mentor: User,
    data: MentorChecklistItemCreate,
) -> MentorChecklistItemResponse:
    team = _require_mentor_team_assignment(db, mentor, data.team_id)
    scoped = _mentor_scope_values(db, team, data.model_dump())
    item = ChecklistItem(
        **scoped,
        created_by_user_id=mentor.id,
        category="Mentor Assigned",
    )
    db.add(item)
    db.flush()

    if item.is_active:
        from app.services.notifications import create_portal_notification

        fellow_ids = db.scalars(
            select(TeamMembership.user_id).where(TeamMembership.team_id == team.id)
        ).all()
        for fellow_id in set(fellow_ids):
            create_portal_notification(
                db,
                recipient_user_id=fellow_id,
                notification_type="checklist_assigned",
                title="New checklist item",
                message=f"'{item.title}' was assigned to {team.name}.",
                target_url="/notifications",
                metadata={"team_id": str(team.id), "checklist_item_id": str(item.id)},
            )

    db.commit()
    db.refresh(item)
    return _mentor_item_response(db, item, team, mentor)


def _require_owned_mentor_item(
    db: Session,
    mentor: User,
    item_id: UUID,
) -> tuple[ChecklistItem, Team]:
    item = db.get(ChecklistItem, item_id)
    if not item or item.team_id is None:
        raise _not_found("Mentor-created checklist item not found.")
    team = _require_mentor_team_assignment(db, mentor, item.team_id)
    if item.created_by_user_id != mentor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Mentor who created this checklist item may modify it.",
        )
    return item, team


def update_mentor_checklist_item(
    db: Session,
    mentor: User,
    item_id: UUID,
    data: MentorChecklistItemUpdate,
) -> MentorChecklistItemResponse:
    item, team = _require_owned_mentor_item(db, mentor, item_id)
    updates = data.model_dump(exclude_unset=True)
    for required_field in ("phase_id", "week_id", "session_id", "title", "is_required", "sequence"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null.")
    resulting = {
        "phase_id": updates.get("phase_id", item.phase_id),
        "week_id": updates.get("week_id", item.week_id),
        "session_id": updates.get("session_id", item.session_id),
    }
    _mentor_scope_values(db, team, resulting)
    for field, value in updates.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return _mentor_item_response(db, item, team, mentor)


def set_mentor_checklist_item_activation(
    db: Session,
    mentor: User,
    item_id: UUID,
    data: ChecklistActivationUpdate,
) -> MentorChecklistItemResponse:
    item, team = _require_owned_mentor_item(db, mentor, item_id)
    item.is_active = data.is_active
    db.commit()
    db.refresh(item)
    return _mentor_item_response(db, item, team, mentor)
