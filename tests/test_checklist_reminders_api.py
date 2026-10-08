from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.security import create_access_token, hash_password
from app.models.checklist import ChecklistItem, FellowChecklistCompletion
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionType
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week


@pytest.fixture
def checklist_context(db):
    suffix = uuid4().hex[:8]
    program = Program(name="Checklist Program", code=f"CHECK-{suffix}")
    db.add(program)
    db.flush()

    discover = Phase(
        program_id=program.id,
        code="DISCOVER",
        name="DISCOVER",
        development_role="THINK",
        sequence=1,
        duration_weeks=4,
        is_active=True,
    )
    validate = Phase(
        program_id=program.id,
        code="VALIDATE",
        name="VALIDATE",
        development_role="PROVE",
        sequence=2,
        duration_weeks=4,
        is_active=True,
    )
    db.add_all([discover, validate])
    db.flush()

    week = Week(
        phase_id=discover.id,
        week_number=1,
        title="Discover the problem",
        sequence=1,
    )
    cohort = Cohort(
        program_id=program.id,
        name="Checklist Cohort",
        code=f"CC-{suffix}",
        start_date=date.today(),
        status=CohortStatus.ACTIVE,
    )
    other_cohort = Cohort(
        program_id=program.id,
        name="Other Cohort",
        code=f"OC-{suffix}",
        start_date=date.today(),
        status=CohortStatus.ACTIVE,
    )
    db.add_all([week, cohort, other_cohort])
    db.flush()

    session = DBSession(
        cohort_id=cohort.id,
        phase_id=discover.id,
        week_id=week.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Problem framing",
        sequence=1,
    )
    db.add(session)

    fellows = []
    for number in (1, 2):
        fellow = User(
            first_name=f"Fellow{number}",
            last_name="Checklist",
            email=f"checklist-fellow-{number}-{suffix}@example.com",
            password_hash=hash_password("FellowSecret123!"),
            role=UserRole.FELLOW,
            account_status=AccountStatus.ACTIVE,
            is_active=True,
        )
        db.add(fellow)
        db.flush()
        db.add(
            Enrollment(
                user_id=fellow.id,
                cohort_id=cohort.id,
                enrollment_status=EnrollmentStatus.ACTIVE,
            )
        )
        fellows.append(fellow)

    db.commit()
    for entity in [program, discover, validate, week, cohort, other_cohort, session, *fellows]:
        db.refresh(entity)

    yield {
        "program": program,
        "phase": discover,
        "other_phase": validate,
        "week": week,
        "cohort": cohort,
        "other_cohort": other_cohort,
        "session": session,
        "fellows": fellows,
    }

    db.rollback()
    fellow_ids = [fellow.id for fellow in fellows]
    db.execute(
        delete(FellowChecklistCompletion).where(
            FellowChecklistCompletion.user_id.in_(fellow_ids)
        )
    )
    item_ids = db.info.pop("checklist_test_item_ids", [])
    if item_ids:
        db.execute(delete(ChecklistItem).where(ChecklistItem.id.in_(item_ids)))
    db.execute(delete(User).where(User.id.in_(fellow_ids)))
    db.execute(delete(Program).where(Program.id == program.id))
    db.commit()


def fellow_headers(fellow: User) -> dict[str, str]:
    token = create_access_token(str(fellow.id), role=fellow.role.value)
    return {"Authorization": f"Bearer {token}"}


def add_item(db, **values) -> ChecklistItem:
    item = ChecklistItem(title=values.pop("title", "Checklist task"), **values)
    db.add(item)
    db.commit()
    db.refresh(item)
    db.info.setdefault("checklist_test_item_ids", []).append(item.id)
    return item


def test_checklist_reminders_authorization(client, checklist_context, admin_headers):
    context = checklist_context
    fellow = context["fellows"][0]

    # Anonymous user should get 401
    resp = client.get("/api/v1/fellow/checklist-reminders")
    assert resp.status_code == 401

    # Admin user should get 403 (endpoint is restricted to Fellow Portal)
    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=admin_headers,
    )
    assert resp.status_code == 403

    # Authenticated fellow should get 200
    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_count" in data
    assert "reminders" in data
    assert data["total_count"] == 0
    assert data["reminders"] == []


def test_reminders_empty_when_no_active_reminders(client, checklist_context):
    fellow = checklist_context["fellows"][0]
    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    assert resp.json() == {"total_count": 0, "reminders": []}


def test_reminders_includes_overdue_and_upcoming_within_48h(client, db, checklist_context):
    fellow = checklist_context["fellows"][0]
    now = datetime.now(timezone.utc)

    # 1. Overdue item (2 hours ago)
    overdue_item = add_item(
        db,
        title="Overdue submission",
        due_at=now - timedelta(hours=2),
        sequence=1,
    )

    # 2. Upcoming item (within 48 hours, e.g. 24 hours from now)
    upcoming_item = add_item(
        db,
        title="Upcoming quiz",
        due_at=now + timedelta(hours=24),
        sequence=2,
    )

    # 3. Far future item (> 48 hours, e.g. 72 hours from now) - should NOT be included
    add_item(
        db,
        title="Future milestone",
        due_at=now + timedelta(hours=72),
        sequence=3,
    )

    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 2
    assert len(data["reminders"]) == 2

    # Overdue reminder
    r_overdue = data["reminders"][0]
    assert r_overdue["id"] == str(overdue_item.id)
    assert r_overdue["title"] == "Overdue submission"
    assert r_overdue["status"] == "overdue"
    assert r_overdue["message"] == "This checklist item is overdue."
    assert r_overdue["target_url"] == "/notifications"

    # Upcoming reminder
    r_upcoming = data["reminders"][1]
    assert r_upcoming["id"] == str(upcoming_item.id)
    assert r_upcoming["title"] == "Upcoming quiz"
    assert r_upcoming["status"] == "pending"
    assert r_upcoming["message"] == "This checklist item is due soon."
    assert r_upcoming["target_url"] == "/notifications"


def test_reminders_excludes_completed_items(client, db, checklist_context):
    fellow = checklist_context["fellows"][0]
    now = datetime.now(timezone.utc)

    # Overdue item that is completed
    completed_overdue = add_item(
        db,
        title="Completed overdue task",
        due_at=now - timedelta(hours=5),
    )
    # Upcoming item that is completed
    completed_upcoming = add_item(
        db,
        title="Completed upcoming task",
        due_at=now + timedelta(hours=10),
    )
    # Active upcoming item (not completed)
    active_upcoming = add_item(
        db,
        title="Still pending task",
        due_at=now + timedelta(hours=12),
    )

    # Mark first two as completed by fellow
    db.add(
        FellowChecklistCompletion(
            checklist_item_id=completed_overdue.id,
            user_id=fellow.id,
            is_completed=True,
            completed_at=now - timedelta(hours=1),
        )
    )
    db.add(
        FellowChecklistCompletion(
            checklist_item_id=completed_upcoming.id,
            user_id=fellow.id,
            is_completed=True,
            completed_at=now - timedelta(minutes=30),
        )
    )
    db.commit()

    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert len(data["reminders"]) == 1
    assert data["reminders"][0]["id"] == str(active_upcoming.id)


def test_reminders_excludes_undated_items(client, db, checklist_context):
    fellow = checklist_context["fellows"][0]

    # Undated item (due_at is None)
    add_item(
        db,
        title="Undated checklist task",
        due_at=None,
    )

    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 0
    assert data["reminders"] == []


def test_reminders_ordering_and_5_item_limit(client, db, checklist_context):
    fellow = checklist_context["fellows"][0]
    now = datetime.now(timezone.utc)

    # Create 4 overdue items
    overdue_items = [
        add_item(db, title=f"Overdue {i}", due_at=now - timedelta(days=5 - i), sequence=i)
        for i in range(1, 5)
    ]
    # Create 4 upcoming items within 48h
    upcoming_items = [
        add_item(db, title=f"Upcoming {i}", due_at=now + timedelta(hours=i * 6), sequence=10 + i)
        for i in range(1, 5)
    ]

    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()

    # Total actionable count must be 8 (4 overdue + 4 upcoming)
    assert data["total_count"] == 8

    # Reminders list must be capped at 5
    assert len(data["reminders"]) == 5

    # Top 4 must be overdue items in ascending deadline order
    for idx in range(4):
        assert data["reminders"][idx]["id"] == str(overdue_items[idx].id)
        assert data["reminders"][idx]["status"] == "overdue"

    # 5th item must be the earliest upcoming item (upcoming_items[0])
    assert data["reminders"][4]["id"] == str(upcoming_items[0].id)
    assert data["reminders"][4]["status"] == "pending"


def test_reminders_fellow_and_cohort_isolation(client, db, checklist_context):
    context = checklist_context
    fellow1 = context["fellows"][0]
    fellow2 = context["fellows"][1]
    other_cohort = context["other_cohort"]
    now = datetime.now(timezone.utc)

    # Item assigned to other_cohort should not be visible to fellow1
    add_item(
        db,
        title="Other cohort task",
        cohort_id=other_cohort.id,
        due_at=now - timedelta(hours=1),
    )

    # Item visible to both fellows in the cohort
    shared_item = add_item(
        db,
        title="Shared cohort task",
        cohort_id=context["cohort"].id,
        due_at=now - timedelta(hours=2),
    )

    # Fellow 1 sees shared_item
    resp1 = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow1),
    )
    assert resp1.status_code == 200
    assert resp1.json()["total_count"] == 1
    assert resp1.json()["reminders"][0]["id"] == str(shared_item.id)

    # Fellow 2 completes the item
    db.add(
        FellowChecklistCompletion(
            checklist_item_id=shared_item.id,
            user_id=fellow2.id,
            is_completed=True,
            completed_at=now,
        )
    )
    db.commit()

    # Fellow 2 has 0 reminders
    resp2 = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow2),
    )
    assert resp2.status_code == 200
    assert resp2.json()["total_count"] == 0

    # Fellow 1 STILL has 1 reminder (unaffected by Fellow 2's completion)
    resp1_after = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow1),
    )
    assert resp1_after.status_code == 200
    assert resp1_after.json()["total_count"] == 1
    assert resp1_after.json()["reminders"][0]["id"] == str(shared_item.id)


def test_reminders_timezone_and_boundary_behavior(client, db, checklist_context):
    fellow = checklist_context["fellows"][0]
    now = datetime.now(timezone.utc)

    # Item exactly at 48 hours boundary
    add_item(
        db,
        title="Boundary 48h task",
        due_at=now + timedelta(hours=48),
    )

    # Item at 48 hours + 10 minutes (outside boundary)
    add_item(
        db,
        title="Outside boundary task",
        due_at=now + timedelta(hours=48, minutes=10),
    )

    # Item with non-UTC offset (IST: +05:30) within 48h
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_now = datetime.now(ist_tz)
    ist_item = add_item(
        db,
        title="IST timezone task",
        due_at=ist_now + timedelta(hours=20),
    )

    resp = client.get(
        "/api/v1/fellow/checklist-reminders",
        headers=fellow_headers(fellow),
    )
    assert resp.status_code == 200
    data = resp.json()

    # Boundary task and IST task are included (2 items), outside boundary task is excluded
    assert data["total_count"] == 2
    reminder_titles = [r["title"] for r in data["reminders"]]
    assert "Boundary 48h task" in reminder_titles
    assert "IST timezone task" in reminder_titles
    assert "Outside boundary task" not in reminder_titles
