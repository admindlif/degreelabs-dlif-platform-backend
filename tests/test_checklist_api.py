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


def test_fellow_list_filters_scopes_and_calculates_summary(client, db, checklist_context):
    context = checklist_context
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expected = [
        add_item(db, title="Global", sequence=0),
        add_item(db, title="Cohort", cohort_id=context["cohort"].id, sequence=1),
        add_item(db, title="Phase", phase_id=context["phase"].id, sequence=2),
        add_item(db, title="Week", week_id=context["week"].id, sequence=3),
        add_item(db, title="Session", session_id=context["session"].id, due_at=past, sequence=4),
    ]
    add_item(db, title="Other cohort", cohort_id=context["other_cohort"].id)
    add_item(db, title="Other phase", phase_id=context["other_phase"].id)
    add_item(db, title="Inactive", is_active=False)

    response = client.get(
        "/api/v1/fellow/checklist",
        headers=fellow_headers(context["fellows"][0]),
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(item.id) for item in expected]
    assert body["items"][-1]["status"] == "overdue"
    assert body["summary"] == {
        "total": 5,
        "completed": 0,
        "pending": 5,
        "overdue": 1,
        "percentage": 0,
    }


def test_completion_is_isolated_per_fellow_and_can_be_cleared(client, db, checklist_context):
    item = add_item(
        db,
        cohort_id=checklist_context["cohort"].id,
        due_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    fellow_a, fellow_b = checklist_context["fellows"]

    completed = client.put(
        f"/api/v1/fellow/checklist/{item.id}/completion",
        headers=fellow_headers(fellow_a),
        json={"is_completed": True},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None

    fellow_b_list = client.get(
        "/api/v1/fellow/checklist", headers=fellow_headers(fellow_b)
    ).json()
    assert fellow_b_list["items"][0]["is_completed"] is False
    assert fellow_b_list["items"][0]["status"] == "overdue"

    cleared = client.put(
        f"/api/v1/fellow/checklist/{item.id}/completion",
        headers=fellow_headers(fellow_a),
        json={"is_completed": False},
    )
    assert cleared.status_code == 200
    assert cleared.json()["completed_at"] is None
    assert cleared.json()["status"] == "overdue"


def test_summary_percentage_uses_current_fellow_only(client, db, checklist_context):
    items = [add_item(db, title=f"Item {number}", sequence=number) for number in range(3)]
    fellow = checklist_context["fellows"][0]
    for item in items[:2]:
        response = client.put(
            f"/api/v1/fellow/checklist/{item.id}/completion",
            headers=fellow_headers(fellow),
            json={"is_completed": True},
        )
        assert response.status_code == 200

    summary = client.get(
        "/api/v1/fellow/checklist", headers=fellow_headers(fellow)
    ).json()["summary"]
    assert summary == {
        "total": 3,
        "completed": 2,
        "pending": 1,
        "overdue": 0,
        "percentage": 67,
    }


def test_fellow_cannot_complete_item_outside_scope(client, db, checklist_context):
    item = add_item(db, cohort_id=checklist_context["other_cohort"].id)
    response = client.put(
        f"/api/v1/fellow/checklist/{item.id}/completion",
        headers=fellow_headers(checklist_context["fellows"][0]),
        json={"is_completed": True},
    )
    assert response.status_code == 404
    assert db.query(FellowChecklistCompletion).count() == 0


def test_checklist_requires_fellow_access(client, admin_headers, checklist_context):
    assert client.get("/api/v1/fellow/checklist").status_code == 401
    assert client.get(
        "/api/v1/fellow/checklist", headers=admin_headers
    ).status_code == 403
    assert client.get(
        "/api/v1/admin/checklist-items",
        headers=fellow_headers(checklist_context["fellows"][0]),
    ).status_code == 403


def test_invalid_checklist_item_returns_404(client, checklist_context):
    response = client.put(
        f"/api/v1/fellow/checklist/{uuid4()}/completion",
        headers=fellow_headers(checklist_context["fellows"][0]),
        json={"is_completed": True},
    )
    assert response.status_code == 404


def test_admin_checklist_crud(client, admin_headers, checklist_context):
    payload = {
        "cohort_id": str(checklist_context["cohort"].id),
        "phase_id": str(checklist_context["phase"].id),
        "week_id": str(checklist_context["week"].id),
        "session_id": str(checklist_context["session"].id),
        "title": "Submit reflection",
        "description": "Share the weekly reflection.",
        "category": "Reflection",
        "action_label": "Open session",
        "action_url": f"/sessions/{checklist_context['session'].id}",
        "is_required": True,
        "is_active": True,
        "sequence": 4,
    }
    created = client.post(
        "/api/v1/admin/checklist-items", headers=admin_headers, json=payload
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    listed = client.get("/api/v1/admin/checklist-items", headers=admin_headers)
    assert listed.status_code == 200
    assert item_id in [item["id"] for item in listed.json()]

    updated = client.put(
        f"/api/v1/admin/checklist-items/{item_id}",
        headers=admin_headers,
        json={"title": "Submit final reflection", "is_required": False},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Submit final reflection"
    assert updated.json()["is_required"] is False

    deleted = client.delete(
        f"/api/v1/admin/checklist-items/{item_id}", headers=admin_headers
    )
    assert deleted.status_code == 204


@pytest.mark.parametrize(
    "unsafe_url",
    ["javascript:alert(1)", "//evil.example/path", "ftp://evil.example/file"],
)
def test_admin_rejects_unsafe_action_urls(client, admin_headers, unsafe_url):
    response = client.post(
        "/api/v1/admin/checklist-items",
        headers=admin_headers,
        json={"title": "Unsafe", "action_url": unsafe_url},
    )
    assert response.status_code == 422


def test_admin_scope_validation_rejects_mismatched_cohort(
    client, admin_headers, checklist_context
):
    response = client.post(
        "/api/v1/admin/checklist-items",
        headers=admin_headers,
        json={
            "title": "Invalid scope",
            "cohort_id": str(checklist_context["other_cohort"].id),
            "session_id": str(checklist_context["session"].id),
        },
    )
    assert response.status_code == 422
