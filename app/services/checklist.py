from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.checklist import ChecklistItem, FellowChecklistCompletion
from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.session import Session as DBSession
from app.models.user import User
from app.models.week import Week
from app.repositories.discover import get_phase_by_code
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.checklist import (
    ChecklistItemCreate,
    ChecklistItemUpdate,
    ChecklistReminderItem,
    ChecklistRemindersResponse,
    ChecklistSummaryResponse,
    FellowChecklistItemResponse,
    FellowChecklistResponse,
)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _fellow_scope(
    db: Session,
    current_user: User,
) -> tuple[UUID, UUID | None]:
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise _not_found("No active enrollment found for this Fellow.")

    phase = get_phase_by_code(
        db,
        enrollment.cohort.program_id,
        code="DISCOVER",
    )
    return enrollment.cohort_id, phase.id if phase else None


def _visibility_conditions(
    cohort_id: UUID,
    phase_id: UUID | None,
):
    conditions = [
        ChecklistItem.is_active.is_(True),
        or_(
            ChecklistItem.cohort_id.is_(None),
            ChecklistItem.cohort_id == cohort_id,
        ),
    ]

    if phase_id is None:
        conditions.extend(
            [
                ChecklistItem.phase_id.is_(None),
                ChecklistItem.week_id.is_(None),
                ChecklistItem.session_id.is_(None),
            ]
        )
    else:
        week_ids = select(Week.id).where(Week.phase_id == phase_id)
        session_ids = select(DBSession.id).where(
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
                ),
            ]
        )

    return conditions


def _status_for(
    item: ChecklistItem,
    completion: FellowChecklistCompletion | None,
    now: datetime,
) -> str:
    if completion and completion.is_completed:
        return "completed"
    if item.due_at and item.due_at < now:
        return "overdue"
    return "pending"


def _fellow_item_response(
    item: ChecklistItem,
    completion: FellowChecklistCompletion | None,
    now: datetime,
) -> FellowChecklistItemResponse:
    is_completed = bool(completion and completion.is_completed)
    return FellowChecklistItemResponse(
        id=item.id,
        title=item.title,
        description=item.description,
        category=item.category,
        due_at=item.due_at,
        status=_status_for(item, completion, now),
        is_required=item.is_required,
        is_completed=is_completed,
        completed_at=completion.completed_at if is_completed else None,
        action_label=item.action_label,
        action_url=item.action_url,
        sequence=item.sequence,
    )


def get_fellow_checklist(
    db: Session,
    current_user: User,
) -> FellowChecklistResponse:
    cohort_id, phase_id = _fellow_scope(db, current_user)
    rows = db.execute(
        select(ChecklistItem, FellowChecklistCompletion)
        .outerjoin(
            FellowChecklistCompletion,
            (FellowChecklistCompletion.checklist_item_id == ChecklistItem.id)
            & (FellowChecklistCompletion.user_id == current_user.id),
        )
        .where(*_visibility_conditions(cohort_id, phase_id))
        .order_by(
            ChecklistItem.sequence,
            ChecklistItem.due_at.asc().nulls_last(),
            ChecklistItem.created_at,
        )
    ).all()

    now = datetime.now(timezone.utc)
    items = [
        _fellow_item_response(item, completion, now)
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
    cohort_id, phase_id = _fellow_scope(db, current_user)
    rows = db.execute(
        select(ChecklistItem, FellowChecklistCompletion)
        .outerjoin(
            FellowChecklistCompletion,
            (FellowChecklistCompletion.checklist_item_id == ChecklistItem.id)
            & (FellowChecklistCompletion.user_id == current_user.id),
        )
        .where(*_visibility_conditions(cohort_id, phase_id))
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
) -> FellowChecklistItemResponse:
    cohort_id, phase_id = _fellow_scope(db, current_user)
    item = db.scalar(
        select(ChecklistItem).where(
            ChecklistItem.id == item_id,
            *_visibility_conditions(cohort_id, phase_id),
        )
    )
    if not item:
        raise _not_found("Checklist item not found.")

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
        )
        db.add(completion)

    completion.is_completed = is_completed
    completion.completed_at = now if is_completed else None
    db.commit()
    db.refresh(completion)
    return _fellow_item_response(item, completion, now)


def _validate_scope(
    db: Session,
    values: dict,
) -> None:
    cohort = db.get(Cohort, values.get("cohort_id")) if values.get("cohort_id") else None
    phase = db.get(Phase, values.get("phase_id")) if values.get("phase_id") else None
    week = db.get(Week, values.get("week_id")) if values.get("week_id") else None
    session = db.get(DBSession, values.get("session_id")) if values.get("session_id") else None

    if values.get("cohort_id") and not cohort:
        raise _not_found("Cohort not found.")
    if values.get("phase_id") and not phase:
        raise _not_found("Phase not found.")
    if values.get("week_id") and not week:
        raise _not_found("Week not found.")
    if values.get("session_id") and not session:
        raise _not_found("Session not found.")

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
) -> ChecklistItem:
    values = data.model_dump()
    _validate_scope(db, values)
    item = ChecklistItem(**values)
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

    updates = data.model_dump(exclude_unset=True)
    for required_field in ("title", "is_required", "is_active", "sequence"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{required_field} cannot be null.",
            )
    resulting_values = {
        "cohort_id": updates.get("cohort_id", item.cohort_id),
        "phase_id": updates.get("phase_id", item.phase_id),
        "week_id": updates.get("week_id", item.week_id),
        "session_id": updates.get("session_id", item.session_id),
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
