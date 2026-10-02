from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week
from app.services.discover_initialization import (
    CANONICAL_DISCOVER_SESSIONS,
    CANONICAL_DISCOVER_WEEKS,
    DiscoverSessionInitializationError,
    initialize_discover_sessions_for_cohort,
)


@pytest.fixture
def discover_foundation(db: Session):
    suffix = uuid4().hex[:8]
    program = Program(
        name=f"DISCOVER Initialization Test {suffix}",
        code=f"INIT-{suffix}",
        is_active=True,
    )
    db.add(program)
    db.flush()

    phase = Phase(
        program_id=program.id,
        code="DISCOVER",
        name="DISCOVER",
        development_role="THINK",
        sequence=1,
        duration_weeks=4,
        is_active=True,
    )
    db.add(phase)
    db.flush()

    weeks: dict[int, Week] = {}
    for week_number in range(1, 5):
        week = Week(
            phase_id=phase.id,
            week_number=week_number,
            title=f"Week {week_number}",
            sequence=week_number,
        )
        db.add(week)
        db.flush()
        weeks[week_number] = week

    db.commit()
    created_user_ids = []

    yield {
        "program": program,
        "phase": phase,
        "weeks": weeks,
        "created_user_ids": created_user_ids,
    }

    for user_id in created_user_ids:
        user = db.get(User, user_id)
        if user:
            db.delete(user)
    db.flush()

    existing_program = db.get(Program, program.id)
    if existing_program:
        db.delete(existing_program)
    db.commit()


def create_cohort(
    db: Session,
    foundation,
    label: str,
) -> Cohort:
    suffix = uuid4().hex[:8]
    cohort = Cohort(
        program_id=foundation["program"].id,
        name=f"{label} {suffix}",
        code=f"{label}-{suffix}",
        status=CohortStatus.ACTIVE,
    )
    db.add(cohort)
    db.flush()
    return cohort


def create_enrolled_fellow(
    db: Session,
    foundation,
    cohort: Cohort,
) -> User:
    suffix = uuid4().hex[:8]
    fellow = User(
        first_name="Canonical",
        last_name="Fellow",
        email=f"canonical_{suffix}@example.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(fellow)
    db.flush()
    foundation["created_user_ids"].append(fellow.id)
    db.add(
        Enrollment(
            user_id=fellow.id,
            cohort_id=cohort.id,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
    )
    db.commit()
    return fellow


def fellow_headers(fellow: User) -> dict[str, str]:
    token = create_access_token(
        subject=str(fellow.id),
        role=fellow.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


def test_initializer_creates_s0_through_s12_once_and_is_idempotent(
    db: Session,
    discover_foundation,
):
    cohort = create_cohort(db, discover_foundation, "IDEMPOTENT")

    first_result = initialize_discover_sessions_for_cohort(db, cohort)
    second_result = initialize_discover_sessions_for_cohort(db, cohort)
    db.commit()

    sessions = list(
        db.scalars(
            select(DBSession)
            .where(DBSession.cohort_id == cohort.id)
            .order_by(DBSession.session_number)
        ).all()
    )

    assert first_result.created_session_numbers == tuple(range(13))
    assert first_result.synced_week_numbers == (1, 2, 3, 4)
    assert second_result.created_session_numbers == ()
    assert second_result.synced_session_numbers == ()
    assert second_result.unchanged_session_numbers == tuple(range(13))
    assert second_result.created_week_numbers == ()
    assert second_result.synced_week_numbers == ()
    assert second_result.unchanged_week_numbers == (1, 2, 3, 4)
    assert len(sessions) == 13
    assert [item.session_number for item in sessions] == list(range(13))
    assert [item.title for item in sessions] == [
        spec.title for spec in CANONICAL_DISCOVER_SESSIONS
    ]
    assert [item.description for item in sessions] == [
        spec.description for spec in CANONICAL_DISCOVER_SESSIONS
    ]
    session_three = next(item for item in sessions if item.session_number == 3)
    assert session_three.title == (
        "Business Diagnosis & Problem Framing Pack Review"
    )
    assert session_three.session_type == SessionType.OUTPUT_REVIEW
    assert session_three.week_id == discover_foundation["weeks"][1].id

    for item in sessions:
        if item.session_number <= 2:
            assert item.is_unlocked is True
            assert item.unlock_at is not None
        else:
            assert item.is_unlocked is False
            assert item.unlock_at is None


def test_initializer_repairs_incomplete_weeks_before_creating_sessions(
    db: Session,
    discover_foundation,
):
    phase = discover_foundation["phase"]
    week_one = discover_foundation["weeks"][1]
    week_one.sequence = 9
    week_one.title = "Drifted Week 1"
    week_one.strategic_question = "Drifted question?"
    week_one.description = "Drifted description"
    week_one.unlock_at = datetime.now(timezone.utc) + timedelta(days=1)
    for week_number in (2, 3, 4):
        db.delete(discover_foundation["weeks"].pop(week_number))
    db.commit()
    db.refresh(week_one)

    original_id = week_one.id
    original_unlock_at = week_one.unlock_at
    original_created_at = week_one.created_at
    original_updated_at = week_one.updated_at
    cohort = create_cohort(db, discover_foundation, "REPAIR-WEEKS")

    first_result = initialize_discover_sessions_for_cohort(db, cohort)
    second_result = initialize_discover_sessions_for_cohort(db, cohort)
    db.commit()
    db.refresh(week_one)

    weeks = list(
        db.scalars(
            select(Week)
            .where(Week.phase_id == phase.id)
            .order_by(Week.week_number)
        ).all()
    )
    assert first_result.created_week_numbers == (2, 3, 4)
    assert first_result.synced_week_numbers == (1,)
    assert first_result.unchanged_week_numbers == ()
    assert first_result.created_session_numbers == tuple(range(13))
    assert second_result.created_week_numbers == ()
    assert second_result.synced_week_numbers == ()
    assert second_result.unchanged_week_numbers == (1, 2, 3, 4)
    assert second_result.created_session_numbers == ()
    assert second_result.unchanged_session_numbers == tuple(range(13))
    assert len(weeks) == 4
    assert week_one.id == original_id
    assert week_one.unlock_at == original_unlock_at
    assert week_one.created_at == original_created_at
    assert week_one.updated_at == original_updated_at
    assert [
        (
            week.week_number,
            week.sequence,
            week.title,
            week.strategic_question,
            week.description,
        )
        for week in weeks
    ] == [
        (
            spec.week_number,
            spec.sequence,
            spec.title,
            spec.strategic_question,
            spec.description,
        )
        for spec in CANONICAL_DISCOVER_WEEKS
    ]


def test_two_cohorts_share_canonical_metadata_but_not_unlock_state(
    db: Session,
    discover_foundation,
):
    cohort_a = create_cohort(db, discover_foundation, "COMMON-A")
    cohort_b = create_cohort(db, discover_foundation, "COMMON-B")
    initialize_discover_sessions_for_cohort(db, cohort_a)
    shared_week_ids = tuple(
        db.scalars(
            select(Week.id)
            .where(Week.phase_id == discover_foundation["phase"].id)
            .order_by(Week.week_number)
        ).all()
    )
    initialize_discover_sessions_for_cohort(db, cohort_b)
    db.commit()

    sessions_a = list(
        db.scalars(
            select(DBSession)
            .where(DBSession.cohort_id == cohort_a.id)
            .order_by(DBSession.session_number)
        ).all()
    )
    sessions_b = list(
        db.scalars(
            select(DBSession)
            .where(DBSession.cohort_id == cohort_b.id)
            .order_by(DBSession.session_number)
        ).all()
    )

    assert len(sessions_a) == len(sessions_b) == 13
    assert shared_week_ids == tuple(
        db.scalars(
            select(Week.id)
            .where(Week.phase_id == discover_foundation["phase"].id)
            .order_by(Week.week_number)
        ).all()
    )
    assert {item.id for item in sessions_a}.isdisjoint(
        {item.id for item in sessions_b}
    )
    assert [
        (
            item.session_number,
            item.session_type,
            item.title,
            item.description,
            item.week_id,
            item.sequence,
        )
        for item in sessions_a
    ] == [
        (
            item.session_number,
            item.session_type,
            item.title,
            item.description,
            item.week_id,
            item.sequence,
        )
        for item in sessions_b
    ]

    session_four_a = sessions_a[4]
    session_four_b = sessions_b[4]
    session_four_a.is_unlocked = True
    session_four_a.unlock_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session_four_b)

    assert session_four_a.is_unlocked is True
    assert session_four_b.is_unlocked is False
    assert session_four_b.unlock_at is None


def test_initializer_syncs_canonical_fields_and_preserves_operational_fields(
    db: Session,
    discover_foundation,
):
    cohort = create_cohort(db, discover_foundation, "PRESERVE")
    phase = discover_foundation["phase"]
    wrong_week = discover_foundation["weeks"][1]
    start_at = datetime.now(timezone.utc) + timedelta(days=2)
    existing = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        week_id=wrong_week.id,
        session_number=4,
        session_type=SessionType.OUTPUT_REVIEW,
        title="Admin-authored Session 4",
        description="Old invented Session 4 description",
        start_at=start_at,
        end_at=start_at + timedelta(hours=2),
        unlock_at=start_at - timedelta(days=1),
        is_unlocked=True,
        submission_enabled=True,
        meeting_url="https://meet.google.com/preserve-me",
        meeting_provider="google_calendar",
        google_meet_space_name="spaces/preserve-me",
        google_meet_code="preserve-me",
        google_calendar_event_id="preserve-event",
        google_calendar_event_url="https://calendar.google.com/preserve-event",
        recording_url="https://drive.google.com/preserve-recording",
        transcript_url="https://drive.google.com/preserve-transcript",
        status=SessionStatus.LIVE,
        sequence=40,
    )
    db.add(existing)
    db.flush()
    existing_id = existing.id

    result = initialize_discover_sessions_for_cohort(db, cohort)
    db.commit()
    db.refresh(existing)

    assert len(
        db.scalars(
            select(DBSession).where(DBSession.cohort_id == cohort.id)
        ).all()
    ) == 13
    assert existing.id == existing_id
    assert existing.phase_id == phase.id
    assert existing.week_id == discover_foundation["weeks"][2].id
    assert existing.session_type == SessionType.LEARN_WORK
    assert existing.title == "Research & Possibility Generation"
    assert existing.sequence == 4
    assert existing.description == (
        "Research synthesis; three distinct possibilities; trade-offs and "
        "constraints."
    )
    assert existing.start_at == start_at
    assert existing.end_at == start_at + timedelta(hours=2)
    assert existing.unlock_at == start_at - timedelta(days=1)
    assert existing.is_unlocked is True
    assert existing.submission_enabled is True
    assert existing.meeting_url == "https://meet.google.com/preserve-me"
    assert existing.meeting_provider == "google_calendar"
    assert existing.google_meet_space_name == "spaces/preserve-me"
    assert existing.google_meet_code == "preserve-me"
    assert existing.google_calendar_event_id == "preserve-event"
    assert existing.google_calendar_event_url == (
        "https://calendar.google.com/preserve-event"
    )
    assert existing.recording_url == "https://drive.google.com/preserve-recording"
    assert existing.transcript_url == "https://drive.google.com/preserve-transcript"
    assert existing.status == SessionStatus.LIVE
    assert result.synced_session_numbers == (4,)
    assert 4 not in result.created_session_numbers


def test_admin_created_cohort_is_initialized_automatically(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    discover_foundation,
):
    suffix = uuid4().hex[:8]
    response = client.post(
        "/api/v1/admin/cohorts",
        headers=admin_headers,
        json={
            "program_id": str(discover_foundation["program"].id),
            "name": f"Automatic {suffix}",
            "code": f"AUTO-{suffix}",
            "status": "active",
        },
    )

    assert response.status_code == 201
    cohort_id = response.json()["id"]
    sessions = list(
        db.scalars(
            select(DBSession)
            .where(DBSession.cohort_id == cohort_id)
            .order_by(DBSession.session_number)
        ).all()
    )
    assert [item.session_number for item in sessions] == list(range(13))


def test_admin_cohort_creation_repairs_incomplete_discover_structure(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    discover_foundation,
):
    missing_week = discover_foundation["weeks"].pop(4)
    db.delete(missing_week)
    db.commit()
    suffix = uuid4().hex[:8]
    cohort_code = f"INCOMPLETE-{suffix}"

    response = client.post(
        "/api/v1/admin/cohorts",
        headers=admin_headers,
        json={
            "program_id": str(discover_foundation["program"].id),
            "name": f"Incomplete {suffix}",
            "code": cohort_code,
            "status": "active",
        },
    )

    assert response.status_code == 201
    cohort = db.scalar(
        select(Cohort).where(Cohort.code == cohort_code)
    )
    assert cohort is not None
    week_four = db.scalar(
        select(Week).where(
            Week.phase_id == discover_foundation["phase"].id,
            Week.week_number == 4,
        )
    )
    assert week_four is not None
    assert week_four.title == "BUILD THE CASE FOR ACTION"
    assert week_four.description is None
    assert set(
        db.scalars(
            select(DBSession.session_number).where(
                DBSession.cohort_id == cohort.id
            )
        ).all()
    ) == set(range(13))


def test_initializer_rejects_missing_active_discover_phase(
    db: Session,
    discover_foundation,
):
    phase = discover_foundation["phase"]
    phase.is_active = False
    missing_week = discover_foundation["weeks"].pop(4)
    db.delete(missing_week)
    cohort = create_cohort(db, discover_foundation, "NO-PHASE")
    db.commit()

    with pytest.raises(
        DiscoverSessionInitializationError,
        match="does not have an active DISCOVER Phase",
    ):
        initialize_discover_sessions_for_cohort(db, cohort)

    assert db.scalar(
        select(Week).where(
            Week.phase_id == phase.id,
            Week.week_number == 4,
        )
    ) is None
    assert db.scalars(
        select(DBSession).where(DBSession.cohort_id == cohort.id)
    ).all() == []


def test_admin_cohort_creation_rolls_back_without_active_discover_phase(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    discover_foundation,
):
    discover_foundation["phase"].is_active = False
    db.commit()
    suffix = uuid4().hex[:8]
    cohort_code = f"NO-ACTIVE-PHASE-{suffix}"

    response = client.post(
        "/api/v1/admin/cohorts",
        headers=admin_headers,
        json={
            "program_id": str(discover_foundation["program"].id),
            "name": f"No active DISCOVER Phase {suffix}",
            "code": cohort_code,
            "status": "active",
        },
    )

    assert response.status_code == 422
    assert "does not have an active DISCOVER Phase" in response.json()[
        "detail"
    ]
    assert db.scalar(
        select(Cohort).where(Cohort.code == cohort_code)
    ) is None


def test_fellow_list_returns_all_sessions_and_masks_locked_summary(
    client: TestClient,
    db: Session,
    discover_foundation,
):
    cohort = create_cohort(db, discover_foundation, "FELLOW-LIST")
    initialize_discover_sessions_for_cohort(db, cohort)
    fellow = create_enrolled_fellow(db, discover_foundation, cohort)
    locked = db.scalar(
        select(DBSession).where(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
    )
    assert locked is not None
    locked.title = "Secret Session 3 Review"
    locked.description = "Secret description"
    locked.start_at = datetime.now(timezone.utc)
    locked.end_at = locked.start_at + timedelta(hours=2)
    locked.meeting_url = "https://meet.google.com/secret"
    locked.recording_url = "https://drive.google.com/secret-recording"
    locked.transcript_url = "https://drive.google.com/secret-transcript"
    locked.submission_enabled = True
    db.commit()

    response = client.get(
        "/api/v1/fellow/sessions",
        headers=fellow_headers(fellow),
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["session_number"] for item in payload] == list(range(13))
    locked_payload = next(
        item for item in payload if item["session_number"] == 3
    )
    assert locked_payload["is_unlocked"] is False
    assert locked_payload["title"] == "Session 3"
    assert locked_payload["description"] is None
    assert locked_payload["start_at"] is None
    assert locked_payload["end_at"] is None
    assert locked_payload["meeting_url"] is None
    assert locked_payload["recording_url"] is None
    assert locked_payload["transcript_url"] is None
    assert locked_payload["submission_enabled"] is False


def test_locked_session_detail_remains_inaccessible(
    client: TestClient,
    db: Session,
    discover_foundation,
):
    cohort = create_cohort(db, discover_foundation, "LOCKED-DETAIL")
    initialize_discover_sessions_for_cohort(db, cohort)
    fellow = create_enrolled_fellow(db, discover_foundation, cohort)
    locked = db.scalar(
        select(DBSession).where(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
    )
    assert locked is not None

    response = client.get(
        f"/api/v1/fellow/sessions/{locked.id}",
        headers=fellow_headers(fellow),
    )

    assert response.status_code == 403
    assert "locked" in response.json()["detail"].lower()


def test_additional_session_is_returned_under_its_assigned_week(
    client: TestClient,
    db: Session,
    discover_foundation,
):
    cohort = create_cohort(db, discover_foundation, "EXTRA-WEEK")
    initialize_discover_sessions_for_cohort(db, cohort)
    fellow = create_enrolled_fellow(db, discover_foundation, cohort)
    phase = discover_foundation["phase"]
    week_two = discover_foundation["weeks"][2]
    extra = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        week_id=week_two.id,
        session_number=13,
        session_type=SessionType.LEARN_WORK,
        title="Additional Week 2 Session",
        description="Custom Cohort-specific Session 13 description",
        status=SessionStatus.SCHEDULED,
        sequence=13,
        is_unlocked=False,
        unlock_at=None,
    )
    db.add(extra)
    db.flush()

    result = initialize_discover_sessions_for_cohort(db, cohort)
    db.commit()
    db.refresh(extra)

    assert result.created_session_numbers == ()
    assert result.synced_session_numbers == ()
    assert result.unchanged_session_numbers == tuple(range(13))
    assert extra.description == "Custom Cohort-specific Session 13 description"

    response = client.get(
        "/api/v1/fellow/discover/weeks",
        headers=fellow_headers(fellow),
    )

    assert response.status_code == 200
    week_two_payload = next(
        item for item in response.json() if item["week_number"] == 2
    )
    all_numbers = {
        item["session_number"]
        for week in response.json()
        for item in week["sessions"]
    }
    assert set(range(13)).issubset(all_numbers)
    assert [
        item["session_number"] for item in week_two_payload["sessions"]
    ] == [4, 5, 6, 13]
    extra_payload = week_two_payload["sessions"][-1]
    assert extra_payload["is_unlocked"] is False
    assert extra_payload["title"] == "Session 13"


def test_admin_can_create_additional_session_beyond_s12(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    discover_foundation,
    monkeypatch,
):
    cohort = create_cohort(db, discover_foundation, "ADMIN-EXTRA")
    other_cohort = create_cohort(
        db, discover_foundation, "ADMIN-EXTRA-OTHER"
    )
    initialize_discover_sessions_for_cohort(db, cohort)
    initialize_discover_sessions_for_cohort(db, other_cohort)
    db.commit()
    phase = discover_foundation["phase"]
    week_four = discover_foundation["weeks"][4]

    monkeypatch.setattr(
        "app.api.routes.admin.settings.google_calendar_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.api.routes.admin.create_calendar_event_with_meet",
        lambda **_: {
            "event_id": "extra-session-event",
            "calendar_url": "https://calendar.google.com/extra-session",
            "meeting_url": "https://meet.google.com/extra-session",
            "meeting_code": "extra-session",
        },
    )
    start_at = datetime.now(timezone.utc) + timedelta(days=3)

    response = client.post(
        "/api/v1/admin/sessions",
        headers=admin_headers,
        json={
            "cohort_id": str(cohort.id),
            "phase_id": str(phase.id),
            "week_id": str(week_four.id),
            "session_number": 13,
            "session_type": "learn_work",
            "title": "Additional Session",
            "description": "Custom admin-authored Session 13 description",
            "start_at": start_at.isoformat(),
            "end_at": (start_at + timedelta(hours=2)).isoformat(),
            "status": "scheduled",
            "sequence": 13,
        },
    )

    assert response.status_code == 201
    assert response.json()["session_number"] == 13
    created_session = db.scalar(
        select(DBSession).where(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 13,
        )
    )
    assert created_session is not None
    assert created_session.description == (
        "Custom admin-authored Session 13 description"
    )
    session_numbers = set(
        db.scalars(
            select(DBSession.session_number).where(
                DBSession.cohort_id == cohort.id
            )
        ).all()
    )
    assert session_numbers == set(range(14))
    other_session_numbers = set(
        db.scalars(
            select(DBSession.session_number).where(
                DBSession.cohort_id == other_cohort.id
            )
        ).all()
    )
    assert other_session_numbers == set(range(13))
