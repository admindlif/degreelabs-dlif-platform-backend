"""
Tests for Fellow API Milestone 1:
- Program, Cohort, Enrollment
- Phase DISCOVER, Weeks, Sessions
- Context, Overview, Weeks, Session detail
- Cohort isolation and security checks
"""

import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta, date

repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week
from apps.student_api.main import app as student_app
from app.models.resource import (
    Resource,
    ResourceType,
)
from app.models.submission import TeamSubmission
from app.models.feedback import SubmissionFeedback
from app.models.team import (
    Team,
    TeamChallengeResource,
    TeamMembership,
    TeamMemberRole,
)


@pytest.fixture
def student_client():
    return TestClient(student_app)


@pytest.fixture
def fellow_user(db: Session) -> User:
    email = f"fellow_{uuid4().hex[:8]}@degreelabs.com"
    user = User(
        first_name="Samantha",
        last_name="Fellow",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.query(Enrollment).filter(Enrollment.user_id == user.id).delete()
    db.delete(user)
    db.commit()


@pytest.fixture
def mentor_user(db: Session) -> User:
    email = f"mentor_{uuid4().hex[:8]}@degreelabs.com"
    user = User(
        first_name="Marcus",
        last_name="Mentor",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.delete(user)
    db.commit()


@pytest.fixture
def setup_fellow_cohort(db: Session, fellow_user: User):
    """
    Ensure DLIF program, Cohort 2026-A, DISCOVER phase, and fellow enrollment exist.
    """
    program = db.query(Program).filter(Program.code == "DLIF").first()
    if not program:
        program = Program(name="DegreeLabs Impact Fellowship", code="DLIF", is_active=True)
        db.add(program)
        db.commit()
        db.refresh(program)

    cohort = db.query(Cohort).filter(Cohort.code == "2026-A").first()
    if not cohort:
        cohort = Cohort(
            program_id=program.id,
            name="DLIF Cohort 2026-A",
            code="2026-A",
            status=CohortStatus.ACTIVE,
        )
        db.add(cohort)
        db.commit()
        db.refresh(cohort)

    phase = db.query(Phase).filter(Phase.program_id == program.id, Phase.code == "DISCOVER").first()
    if not phase:
        phase = Phase(
            program_id=program.id,
            code="DISCOVER",
            name="DISCOVER",
            development_role="THINK",
            sequence=1,
            duration_weeks=4,
        )
        db.add(phase)
        db.commit()
        db.refresh(phase)

    enrollment = (
        db.query(Enrollment)
        .filter(Enrollment.user_id == fellow_user.id, Enrollment.cohort_id == cohort.id)
        .first()
    )
    if not enrollment:
        enrollment = Enrollment(
            user_id=fellow_user.id,
            cohort_id=cohort.id,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
        db.add(enrollment)
        db.commit()
        db.refresh(enrollment)

            # ---------------------------------------------------------
    # Create the four DISCOVER weeks
    # ---------------------------------------------------------
    week_specs = [
        (1, "DISCOVER THE REAL PROBLEM"),
        (2, "CREATE STRATEGIC POSSIBILITIES"),
        (3, "DESIGN THE STRATEGY"),
        (4, "BUILD THE CASE FOR ACTION"),
    ]

    weeks: dict[int, Week] = {}

    for week_number, title in week_specs:
        week = (
            db.query(Week)
            .filter(
                Week.phase_id == phase.id,
                Week.week_number == week_number,
            )
            .first()
        )

        if not week:
            week = Week(
                phase_id=phase.id,
                week_number=week_number,
                title=title,
                sequence=week_number,
            )
            db.add(week)
            db.flush()
        else:
            # Keep test data deterministic
            week.title = title
            week.sequence = week_number

        weeks[week_number] = week

    db.commit()

    # ---------------------------------------------------------
    # Create Session 0 + 12 DISCOVER sessions
    # ---------------------------------------------------------
    existing_sessions = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.phase_id == phase.id,
        )
        .all()

    
    )

    # Keep Session access state deterministic for every test run.
    for existing_session in existing_sessions:
        existing_session.is_unlocked = (
            existing_session.session_number <= 2
        )

        existing_session.unlock_at = (
            datetime.now(timezone.utc)
            if existing_session.session_number <= 2
            else None
        )

    db.flush()

    existing_numbers = {
        session.session_number
        for session in existing_sessions
    }

    base_time = datetime.now(timezone.utc) + timedelta(days=1)

    # Session 0 - induction
    if 0 not in existing_numbers:
        induction = DBSession(
            cohort_id=cohort.id,
            phase_id=phase.id,
            week_id=None,
            session_number=0,
            session_type=SessionType.INDUCTION,
            title="Session 0 - Induction",
            start_at=base_time,
            end_at=base_time + timedelta(hours=1),
            status=SessionStatus.SCHEDULED,
            sequence=0,
            is_unlocked=True,
            unlock_at=datetime.now(timezone.utc),
        )

        db.add(induction)

    # Sessions 1-12
    for session_number in range(1, 13):
        if session_number in existing_numbers:
            continue

        week_number = ((session_number - 1) // 3) + 1
        week = weeks[week_number]

        start_at = base_time + timedelta(days=session_number)

        session = DBSession(
            cohort_id=cohort.id,
            phase_id=phase.id,
            week_id=week.id,
            session_number=session_number,
            session_type=(
                SessionType.OUTPUT_REVIEW
                if session_number % 3 == 0
                else SessionType.LEARN_WORK
            ),
            title=f"Session {session_number}",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            status=SessionStatus.SCHEDULED,
            sequence=session_number,
            is_unlocked=(session_number <= 2),
            unlock_at=(
                datetime.now(timezone.utc)
                if session_number <= 2
                else None
            ),  
        )

        db.add(session)

    db.commit()

    return {"program": program, "cohort": cohort, "phase": phase, "enrollment": enrollment}


@pytest.fixture
def setup_submission_team(
    db: Session,
    fellow_user: User,
    setup_fellow_cohort,
):
    """
    Create one Team with:
    - fellow_user as Team Lead
    - another Fellow as Team Member
    """

    cohort = setup_fellow_cohort["cohort"]

    team = Team(
        cohort_id=cohort.id,
        name=f"Submission Team {uuid4().hex[:8]}",
        company_name="Test Company",
        company_challenge="Test Challenge",
        is_active=True,
    )

    db.add(team)
    db.flush()

    lead_membership = TeamMembership(
        team_id=team.id,
        cohort_id=cohort.id,
        user_id=fellow_user.id,
        team_role=TeamMemberRole.LEAD,
    )

    db.add(lead_membership)

    member_user = User(
        first_name="Team",
        last_name="Member",
        email=(
            f"team_member_"
            f"{uuid4().hex[:8]}"
            "@degreelabs.com"
        ),
        password_hash=hash_password(
            "Pass12345!"
        ),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )

    db.add(member_user)
    db.flush()

    member_enrollment = Enrollment(
        user_id=member_user.id,
        cohort_id=cohort.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )

    db.add(member_enrollment)

    member_membership = TeamMembership(
        team_id=team.id,
        cohort_id=cohort.id,
        user_id=member_user.id,
        team_role=TeamMemberRole.MEMBER,
    )

    db.add(member_membership)

    db.commit()

    db.refresh(team)
    db.refresh(member_user)

    yield {
        "team": team,
        "lead": fellow_user,
        "member": member_user,
        "cohort": cohort,
    }

    # Cleanup submission test data.
    db.query(TeamSubmission).filter(
        TeamSubmission.team_id == team.id
    ).delete(
        synchronize_session=False
    )

    db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id
    ).delete(
        synchronize_session=False
    )

    db.query(Enrollment).filter(
        Enrollment.user_id == member_user.id
    ).delete(
        synchronize_session=False
    )

    db.delete(member_user)
    db.delete(team)

    db.commit()
def test_fellow_context_success(student_client: TestClient, fellow_user: User, setup_fellow_cohort):
    token = create_access_token(subject=str(fellow_user.id), role=fellow_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    res = student_client.get("/api/v1/fellow/context", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["fellow"]["email"] == fellow_user.email
    assert data["program"]["code"] == "DLIF"
    assert data["cohort"]["code"] == "2026-A"
    assert data["current_phase"]["code"] == "DISCOVER"
    assert data["current_phase"]["development_role"] == "THINK"


def test_fellow_context_rejected_for_mentor(student_client: TestClient, mentor_user: User):
    token = create_access_token(subject=str(mentor_user.id), role=mentor_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    res = student_client.get("/api/v1/fellow/context", headers=headers)
    assert res.status_code == 403


def test_fellow_context_unauthenticated(student_client: TestClient):
    res = student_client.get("/api/v1/fellow/context")
    assert res.status_code == 401


def test_discover_overview(student_client: TestClient, fellow_user: User, setup_fellow_cohort):
    token = create_access_token(subject=str(fellow_user.id), role=fellow_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    res = student_client.get("/api/v1/fellow/discover/overview", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["phase"]["code"] == "DISCOVER"
    assert data["cohort"]["code"] == "2026-A"
    assert "progress" in data
    assert data["progress"]["total_weeks"] == 4
    assert data["progress"]["total_sessions"] == 12
    assert "next_session" in data


def test_discover_overview_uses_only_canonical_curriculum_progress(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]
    phase = setup_fellow_cohort["phase"]
    sessions = {
        session.session_number: session
        for session in db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.phase_id == phase.id,
            DBSession.session_number.between(0, 12),
        )
        .all()
    }
    assert set(sessions) == set(range(13))

    original_values = {
        number: (session.status, session.end_at)
        for number, session in sessions.items()
    }
    extra_session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=13,
        session_type=SessionType.LEARN_WORK,
        title="Additional Session 13",
        status=SessionStatus.COMPLETED,
        sequence=13,
    )
    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )
    headers = {"Authorization": f"Bearer {token}"}

    def get_overview():
        response = student_client.get(
            "/api/v1/fellow/discover/overview",
            headers=headers,
        )
        assert response.status_code == 200
        return response.json()

    try:
        for session in sessions.values():
            session.status = SessionStatus.SCHEDULED
        sessions[1].end_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.add(extra_session)
        db.commit()

        overview = get_overview()
        assert overview["progress"] == {
            "current_week": 1,
            "total_weeks": 4,
            "percentage": 0,
            "completed_sessions": 0,
            "total_sessions": 12,
        }
        assert overview["next_session"]["session_number"] == 0

        sessions[0].status = SessionStatus.COMPLETED
        db.commit()
        overview = get_overview()
        assert overview["progress"]["completed_sessions"] == 0
        assert overview["progress"]["percentage"] == 0
        assert overview["progress"]["current_week"] == 1

        for number in range(1, 4):
            sessions[number].status = SessionStatus.COMPLETED
        db.commit()
        overview = get_overview()
        assert overview["progress"]["completed_sessions"] == 3
        assert overview["progress"]["total_sessions"] == 12
        assert overview["progress"]["percentage"] == 25
        assert overview["progress"]["current_week"] == 2

        for number in range(4, 7):
            sessions[number].status = SessionStatus.COMPLETED
        db.commit()
        overview = get_overview()
        assert overview["progress"]["completed_sessions"] == 6
        assert overview["progress"]["percentage"] == 50
        assert overview["progress"]["current_week"] == 3

        for number in range(7, 10):
            sessions[number].status = SessionStatus.COMPLETED
        db.commit()
        overview = get_overview()
        assert overview["progress"]["completed_sessions"] == 9
        assert overview["progress"]["percentage"] == 75
        assert overview["progress"]["current_week"] == 4

        for number in range(10, 13):
            sessions[number].status = SessionStatus.COMPLETED
        db.commit()
        overview = get_overview()
        assert overview["progress"]["completed_sessions"] == 12
        assert overview["progress"]["total_sessions"] == 12
        assert overview["progress"]["percentage"] == 100
        assert overview["progress"]["current_week"] == 4
    finally:
        db.rollback()
        existing_extra = db.get(DBSession, extra_session.id)
        if existing_extra:
            db.delete(existing_extra)
        for number, (original_status, original_end_at) in original_values.items():
            sessions[number].status = original_status
            sessions[number].end_at = original_end_at
        db.commit()


def test_discover_weeks_ordering(student_client: TestClient, fellow_user: User, setup_fellow_cohort):
    token = create_access_token(subject=str(fellow_user.id), role=fellow_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    res = student_client.get("/api/v1/fellow/discover/weeks", headers=headers)
    assert res.status_code == 200
    weeks = res.json()
    assert len(weeks) == 4
    # Check strict sequential ordering
    assert [w["week_number"] for w in weeks] == [1, 2, 3, 4]
    assert weeks[0]["title"] == "DISCOVER THE REAL PROBLEM"
    assert weeks[1]["title"] == "CREATE STRATEGIC POSSIBILITIES"
    assert weeks[2]["title"] == "DESIGN THE STRATEGY"
    assert weeks[3]["title"] == "BUILD THE CASE FOR ACTION"


def test_cross_cohort_session_isolation(db: Session, student_client: TestClient, fellow_user: User, setup_fellow_cohort):
    # Create another separate cohort and a session belonging to it
    other_cohort = Cohort(
        program_id=setup_fellow_cohort["program"].id,
        name="Cohort 2027-B",
        code=f"2027-B-{uuid4().hex[:4]}",
        status=CohortStatus.ACTIVE,
    )
    db.add(other_cohort)
    db.commit()
    db.refresh(other_cohort)

    other_session = DBSession(
        cohort_id=other_cohort.id,
        phase_id=setup_fellow_cohort["phase"].id,
        session_number=99,
        session_type=SessionType.LEARN_WORK,
        title="Secret Other Cohort Session",
        start_at=datetime.now(timezone.utc),
        end_at=datetime.now(timezone.utc) + timedelta(hours=1),
        sequence=99,
    )
    db.add(other_session)
    db.commit()
    db.refresh(other_session)

    token = create_access_token(subject=str(fellow_user.id), role=fellow_user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    # Cross-cohort identifiers are hidden behind the same not-found response.
    res = student_client.get(f"/api/v1/fellow/sessions/{other_session.id}", headers=headers)
    assert res.status_code == 404

    # Cleanup
    db.delete(other_session)
    db.delete(other_cohort)
    db.commit()

def test_locked_session_is_hidden_in_fellow_week_response(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    # Add protected data which must not leak.
    session.title = "SECRET SESSION 3 TITLE"
    session.description = "SECRET DESCRIPTION"
    session.meeting_url = (
        "https://meet.google.com/secret-session-3"
    )
    session.recording_url = (
        "https://drive.google.com/secret-recording"
    )
    session.transcript_url = (
        "https://drive.google.com/secret-transcript"
    )
    session.submission_enabled = True

    session.is_unlocked = False
    session.unlock_at = None

    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        "/api/v1/fellow/discover/weeks",
        headers=headers,
    )

    assert response.status_code == 200

    weeks = response.json()

    all_sessions = [
        item
        for week in weeks
        for item in week["sessions"]
    ]

    session_3 = next(
        item
        for item in all_sessions
        if item["session_number"] == 3
    )

    assert session_3["is_unlocked"] is False
    assert session_3["status"] == "locked"

    assert session_3["title"] == "Session 3"
    assert session_3["description"] is None

    assert session_3["start_at"] is None
    assert session_3["end_at"] is None

    assert session_3["meeting_url"] is None
    assert session_3["recording_url"] is None
    assert session_3["transcript_url"] is None

    assert session_3["submission_enabled"] is False

    assert session_3["has_recording"] is False
    assert session_3["has_transcript"] is False

def test_locked_session_direct_access_is_forbidden(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    session.is_unlocked = False
    session.unlock_at = None

    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        f"/api/v1/fellow/sessions/{session.id}",
        headers=headers,
    )

    assert response.status_code == 403

    assert (
        "locked"
        in response.json()["detail"].lower()
    )

def test_unlocked_session_content_is_available(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    session.title = "Business Diagnosis & Problem Framing Pack Review"
    session.description = "Session 3 description"

    session.meeting_url = (
        "https://meet.google.com/session-3"
    )

    session.recording_url = (
        "https://drive.google.com/session-3-recording"
    )

    session.transcript_url = (
        "https://drive.google.com/session-3-transcript"
    )

    session.submission_enabled = True

    # Simulate Admin unlock.
    session.is_unlocked = True
    session.unlock_at = datetime.now(
        timezone.utc
    )

    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        f"/api/v1/fellow/sessions/{session.id}",
        headers=headers,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["session_number"] == 3
    assert data["is_unlocked"] is True

    assert data["title"] == "Business Diagnosis & Problem Framing Pack Review"

    assert (
        data["meeting_url"]
        == "https://meet.google.com/session-3"
    )

    assert (
        data["recording_url"]
        == "https://drive.google.com/session-3-recording"
    )

    assert (
        data["transcript_url"]
        == "https://drive.google.com/session-3-transcript"
    )

    assert data["submission_enabled"] is True
    assert data["has_recording"] is True
    assert data["has_transcript"] is True

def test_new_fellow_in_cohort_receives_existing_cohort_sessions(
    db: Session,
    student_client: TestClient,
    setup_fellow_cohort,
):
    """
    A Fellow enrolled after Sessions already exist must
    automatically receive all Sessions for that Cohort.
    No per-Session assignment is required.
    """
    cohort = setup_fellow_cohort["cohort"]
    phase = setup_fellow_cohort["phase"]

    new_fellow = User(
        first_name="New",
        last_name="Fellow",
        email=f"new_fellow_{uuid4().hex[:8]}@degreelabs.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )

    db.add(new_fellow)
    db.flush()

    enrollment = Enrollment(
        user_id=new_fellow.id,
        cohort_id=cohort.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )

    db.add(enrollment)
    db.commit()
    db.refresh(new_fellow)

    expected_sessions = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.phase_id == phase.id,
        )
        .all()
    )

    expected_ids = {
        str(session.id)
        for session in expected_sessions
    }

    token = create_access_token(
        subject=str(new_fellow.id),
        role=new_fellow.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        "/api/v1/fellow/discover/weeks",
        headers=headers,
    )

    assert response.status_code == 200

    weeks = response.json()

    received_ids = {
        session["id"]
        for week in weeks
        for session in week["sessions"]
    }

    assert received_ids == expected_ids

    # Cleanup
    db.delete(enrollment)
    db.delete(new_fellow)
    db.commit()

def test_fellow_only_receives_sessions_from_active_cohort(
    db: Session,
    student_client: TestClient,
    setup_fellow_cohort,
):
    """
    Sessions belonging to Cohort A must never appear
    in the Session list of a Fellow enrolled in Cohort B.
    """
    program = setup_fellow_cohort["program"]
    phase = setup_fellow_cohort["phase"]

    other_cohort = Cohort(
        program_id=program.id,
        name="DLIF Cohort Isolation Test",
        code=f"TEST-{uuid4().hex[:8]}",
        status=CohortStatus.ACTIVE,
    )

    db.add(other_cohort)
    db.flush()

    other_fellow = User(
        first_name="Other",
        last_name="Fellow",
        email=f"other_fellow_{uuid4().hex[:8]}@degreelabs.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )

    db.add(other_fellow)
    db.flush()

    other_enrollment = Enrollment(
        user_id=other_fellow.id,
        cohort_id=other_cohort.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )

    db.add(other_enrollment)

    week_1 = (
        db.query(Week)
        .filter(
            Week.phase_id == phase.id,
            Week.week_number == 1,
        )
        .one()
    )

    other_session = DBSession(
        cohort_id=other_cohort.id,
        phase_id=phase.id,
        week_id=week_1.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Cohort B Session",
        start_at=datetime.now(timezone.utc),
        end_at=(
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        ),
        status=SessionStatus.SCHEDULED,
        sequence=1,
        is_unlocked=True,
        unlock_at=datetime.now(timezone.utc),
    )

    db.add(other_session)
    db.commit()

    cohort_a_session_ids = {
        str(session.id)
        for session in (
            db.query(DBSession)
            .filter(
                DBSession.cohort_id
                == setup_fellow_cohort["cohort"].id
            )
            .all()
        )
    }

    token = create_access_token(
        subject=str(other_fellow.id),
        role=other_fellow.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        "/api/v1/fellow/discover/weeks",
        headers=headers,
    )

    assert response.status_code == 200

    weeks = response.json()

    received_ids = {
        session["id"]
        for week in weeks
        for session in week["sessions"]
    }

    # Fellow B sees their own Cohort Session.
    assert str(other_session.id) in received_ids

    # Fellow B sees nothing from Cohort A.
    assert received_ids.isdisjoint(
        cohort_a_session_ids
    )

    # Cleanup
    db.delete(other_session)
    db.delete(other_enrollment)
    db.delete(other_fellow)
    db.delete(other_cohort)
    db.commit()

def test_fellow_sessions_list_contains_only_active_cohort_sessions(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        "/api/v1/fellow/sessions",
        headers=headers,
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) >= 13

    numbers = {
        session["session_number"]
        for session in data
    }

    assert set(range(0, 13)).issubset(
        numbers
    )

    db_session_ids = {
        str(session.id)
        for session in (
            db.query(DBSession)
            .filter(
                DBSession.cohort_id == cohort.id
            )
            .all()
        )
    }

    returned_ids = {
        session["id"]
        for session in data
    }

    assert returned_ids.issubset(
        db_session_ids
    )

    session_3 = next(
        session
        for session in data
        if session["session_number"] == 3
    )

    assert session_3["is_unlocked"] is False
    assert session_3["title"] == "Session 3"
    assert session_3["meeting_url"] is None
def test_unlocked_session_returns_only_its_resources(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]
    phase = setup_fellow_cohort["phase"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 2,
        )
        .one()
    )

    session.is_unlocked = True

    session_resource = Resource(
        phase_id=phase.id,
        session_id=session.id,
        title="Session 2 Presentation",
        resource_type=ResourceType.LINK,
        url="https://drive.google.com/session-2",
        is_active=True,
        sequence=1,
    )

    global_resource = Resource(
        phase_id=phase.id,
        session_id=None,
        title="Global Fellow Handbook",
        resource_type=ResourceType.HANDBOOK,
        url="https://drive.google.com/handbook",
        is_active=True,
        sequence=1,
    )

    db.add_all(
        [
            session_resource,
            global_resource,
        ]
    )

    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/resources"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 200

    data = response.json()

    titles = {
        item["title"]
        for item in data
    }

    assert "Session 2 Presentation" in titles
    assert "Global Fellow Handbook" not in titles

    db.delete(session_resource)
    db.delete(global_resource)
    db.commit()


def test_locked_session_resources_are_forbidden(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]
    phase = setup_fellow_cohort["phase"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    session.is_unlocked = False
    session.unlock_at = None

    protected_resource = Resource(
        phase_id=phase.id,
        session_id=session.id,
        title="Secret Session 3 Resource",
        resource_type=ResourceType.LINK,
        url="https://drive.google.com/secret",
        is_active=True,
        sequence=1,
    )

    db.add(protected_resource)
    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/resources"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 403

    db.delete(protected_resource)
    db.commit()


def test_phase_toolkit_excludes_session_resources(
    db: Session,
    student_client: TestClient,
    fellow_user: User,
    setup_fellow_cohort,
):
    cohort = setup_fellow_cohort["cohort"]
    phase = setup_fellow_cohort["phase"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 1,
        )
        .one()
    )

    session_resource = Resource(
        phase_id=phase.id,
        session_id=session.id,
        title="Session Only Resource",
        resource_type=ResourceType.LINK,
        url="https://drive.google.com/session-only",
        is_active=True,
        sequence=99,
    )

    db.add(session_resource)
    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    response = student_client.get(
        "/api/v1/fellow/resources",
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 200

    titles = {
        item["title"]
        for item in response.json()
    }

    assert "Session Only Resource" not in titles

    db.delete(session_resource)
    db.commit()

def test_team_lead_can_submit_and_resubmit(
    db: Session,
    student_client: TestClient,
    setup_submission_team,
):
    lead = setup_submission_team["lead"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 1,
        )
        .one()
    )

    # Submission must be enabled and Session unlocked.
    session.is_unlocked = True
    session.submission_enabled = True
    db.commit()

    token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    # -----------------------------
    # First submission
    # -----------------------------
    response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "first-submission"
            )
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["is_team_lead"] is True
    assert data["can_submit"] is True

    assert (
        data["submission"]["drive_url"]
        == "https://drive.google.com/first-submission"
    )

    submission_id = data["submission"]["id"]

    # -----------------------------
    # Resubmission
    # -----------------------------
    response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "updated-submission"
            )
        },
    )

    assert response.status_code == 200

    updated = response.json()

    # Same DB record should be updated.
    assert (
        updated["submission"]["id"]
        == submission_id
    )

    assert (
        updated["submission"]["drive_url"]
        == "https://drive.google.com/updated-submission"
    )

    # Only one Team submission should exist.
    assert (
        db.query(TeamSubmission)
        .filter(
            TeamSubmission.team_id
            == setup_submission_team["team"].id,
            TeamSubmission.session_id
            == session.id,
        )
        .count()
        == 1
    )

def test_team_member_can_view_but_cannot_submit(
    db: Session,
    student_client: TestClient,
    setup_submission_team,
):
    lead = setup_submission_team["lead"]
    member = setup_submission_team["member"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 1,
        )
        .one()
    )

    session.is_unlocked = True
    session.submission_enabled = True
    db.commit()

    # --------------------------------------------------
    # Team Lead creates the Team submission first.
    # --------------------------------------------------
    lead_token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    lead_headers = {
        "Authorization": f"Bearer {lead_token}"
    }

    lead_response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=lead_headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "team-submission"
            )
        },
    )

    assert lead_response.status_code == 200

    # --------------------------------------------------
    # Normal Team Member can VIEW it.
    # --------------------------------------------------
    member_token = create_access_token(
        subject=str(member.id),
        role=member.role.value,
    )

    member_headers = {
        "Authorization": f"Bearer {member_token}"
    }

    response = student_client.get(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=member_headers,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["is_team_lead"] is False
    assert data["can_submit"] is False

    assert (
        data["submission"]["drive_url"]
        == "https://drive.google.com/team-submission"
    )

    # --------------------------------------------------
    # Normal Team Member cannot SUBMIT.
    # --------------------------------------------------
    response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=member_headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "member-should-not-submit"
            )
        },
    )

    assert response.status_code == 403

    assert (
        "team lead"
        in response.json()["detail"].lower()
    )

def test_locked_session_submission_is_forbidden(
    db: Session,
    student_client: TestClient,
    setup_submission_team,
):
    lead = setup_submission_team["lead"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    session.is_unlocked = False
    session.unlock_at = None
    session.submission_enabled = True
    db.commit()

    token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.get(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
    )

    assert response.status_code == 403

    assert (
        "locked"
        in response.json()["detail"].lower()
    )

    response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "should-not-submit"
            )
        },
    )

    assert response.status_code == 403

def test_submission_disabled_rejects_team_lead_submit(
    db: Session,
    student_client: TestClient,
    setup_submission_team,
):
    lead = setup_submission_team["lead"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 1,
        )
        .one()
    )

    session.is_unlocked = True
    session.submission_enabled = False
    db.commit()

    token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = student_client.put(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
        json={
            "drive_url": (
                "https://drive.google.com/"
                "should-not-submit"
            )
        },
    )

    assert response.status_code == 403

    assert (
        "not enabled"
        in response.json()["detail"].lower()
    )

    # Viewing the submission area is still allowed
    # because the Session itself is unlocked.
    response = student_client.get(
        f"/api/v1/fellow/sessions/{session.id}/submission",
        headers=headers,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["can_submit"] is False

# ===========================================================================
# FEEDBACK TESTS
# ===========================================================================


@pytest.fixture
def setup_feedback_submission(
    db: Session,
    setup_submission_team,
):
    """
    Create one Team submission for Session 1
    which can then be reviewed by Admin.
    """

    team = setup_submission_team["team"]
    lead = setup_submission_team["lead"]
    member = setup_submission_team["member"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 1,
        )
        .one()
    )

    session.is_unlocked = True
    session.submission_enabled = True

    submission = TeamSubmission(
        session_id=session.id,
        team_id=team.id,
        submitted_by_user_id=lead.id,
        drive_url=(
            "https://drive.google.com/"
            "feedback-test-submission"
        ),
    )

    db.add(submission)
    db.commit()
    db.refresh(submission)

    yield {
        "team": team,
        "lead": lead,
        "member": member,
        "cohort": cohort,
        "session": session,
        "submission": submission,
    }

    # Explicit feedback cleanup first.
    db.query(SubmissionFeedback).filter(
        SubmissionFeedback.submission_id
        == submission.id
    ).delete(
        synchronize_session=False
    )

    db.query(TeamSubmission).filter(
        TeamSubmission.id == submission.id
    ).delete(
        synchronize_session=False
    )

    db.commit()

def test_admin_can_create_and_update_submission_feedback(
    db: Session,
    client: TestClient,
    admin_headers: dict[str, str],
    setup_feedback_submission,
):
    submission = (
        setup_feedback_submission[
            "submission"
        ]
    )

    # --------------------------------------------------
    # First review: Revision Required
    # --------------------------------------------------
    response = client.put(
        (
            f"/api/v1/admin/submissions/"
            f"{submission.id}/feedback"
        ),
        headers=admin_headers,
        json={
            "feedback_text": (
                "Please refine the evidence "
                "and update the working board."
            ),
            "feedback_url": (
                "https://drive.google.com/"
                "feedback-reference"
            ),
            "status": "revision_required",
        },
    )

    assert response.status_code == 200

    first = response.json()

    assert (
        first["submission_id"]
        == str(submission.id)
    )

    assert (
        first["status"]
        == "revision_required"
    )

    feedback_id = first["id"]

    # --------------------------------------------------
    # Update same review: Accepted
    # --------------------------------------------------
    response = client.put(
        (
            f"/api/v1/admin/submissions/"
            f"{submission.id}/feedback"
        ),
        headers=admin_headers,
        json={
            "feedback_text": (
                "Updated submission accepted."
            ),
            "feedback_url": None,
            "status": "accepted",
        },
    )

    assert response.status_code == 200

    updated = response.json()

    # Same feedback row must be updated.
    assert updated["id"] == feedback_id

    assert (
        updated["status"]
        == "accepted"
    )

    assert (
        updated["feedback_text"]
        == "Updated submission accepted."
    )

    # There must still be exactly one feedback row.
    assert (
        db.query(SubmissionFeedback)
        .filter(
            SubmissionFeedback.submission_id
            == submission.id
        )
        .count()
        == 1
    )

def test_team_lead_can_read_submission_feedback(
    db: Session,
    student_client: TestClient,
    setup_feedback_submission,
):
    lead = setup_feedback_submission["lead"]
    session = setup_feedback_submission["session"]
    submission = (
        setup_feedback_submission[
            "submission"
        ]
    )

    feedback = SubmissionFeedback(
        submission_id=submission.id,
        reviewed_by_user_id=None,
        feedback_text=(
            "Please strengthen the evidence."
        ),
        feedback_url=(
            "https://drive.google.com/"
            "lead-feedback"
        ),
        status="revision_required",
    )

    db.add(feedback)
    db.commit()

    token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/feedback"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data is not None

    assert (
        data["feedback_text"]
        == "Please strengthen the evidence."
    )

    assert (
        data["status"]
        == "revision_required"
    )

    assert (
        data["feedback_url"]
        == (
            "https://drive.google.com/"
            "lead-feedback"
        )
    )
def test_team_member_can_read_submission_feedback(
    db: Session,
    student_client: TestClient,
    setup_feedback_submission,
):
    member = setup_feedback_submission[
        "member"
    ]

    session = setup_feedback_submission[
        "session"
    ]

    submission = setup_feedback_submission[
        "submission"
    ]

    feedback = SubmissionFeedback(
        submission_id=submission.id,
        reviewed_by_user_id=None,
        feedback_text=(
            "The Team submission is accepted."
        ),
        feedback_url=None,
        status="accepted",
    )

    db.add(feedback)
    db.commit()

    token = create_access_token(
        subject=str(member.id),
        role=member.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/feedback"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data is not None

    assert (
        data["feedback_text"]
        == "The Team submission is accepted."
    )

    assert data["status"] == "accepted"
def test_other_team_cannot_read_submission_feedback(
    db: Session,
    student_client: TestClient,
    setup_feedback_submission,
):
    cohort = setup_feedback_submission[
        "cohort"
    ]

    session = setup_feedback_submission[
        "session"
    ]

    submission = setup_feedback_submission[
        "submission"
    ]

    feedback = SubmissionFeedback(
        submission_id=submission.id,
        reviewed_by_user_id=None,
        feedback_text=(
            "PRIVATE TEAM ALPHA FEEDBACK"
        ),
        feedback_url=None,
        status="revision_required",
    )

    db.add(feedback)

    # --------------------------------------------------
    # Create another Fellow in the SAME Cohort
    # but place them in a different Team.
    # --------------------------------------------------
    other_fellow = User(
        first_name="Other",
        last_name="Team Fellow",
        email=(
            f"other_team_"
            f"{uuid4().hex[:8]}"
            "@degreelabs.com"
        ),
        password_hash=hash_password(
            "Pass12345!"
        ),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )

    db.add(other_fellow)
    db.flush()

    other_enrollment = Enrollment(
        user_id=other_fellow.id,
        cohort_id=cohort.id,
        enrollment_status=(
            EnrollmentStatus.ACTIVE
        ),
    )

    db.add(other_enrollment)

    other_team = Team(
        cohort_id=cohort.id,
        name=(
            f"Other Team "
            f"{uuid4().hex[:6]}"
        ),
        company_name="Other Company",
        company_challenge="Other Challenge",
        is_active=True,
    )

    db.add(other_team)
    db.flush()

    other_membership = TeamMembership(
        team_id=other_team.id,
        cohort_id=cohort.id,
        user_id=other_fellow.id,
        team_role=TeamMemberRole.MEMBER,
    )

    db.add(other_membership)
    db.commit()

    token = create_access_token(
        subject=str(other_fellow.id),
        role=other_fellow.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/feedback"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 200

    # Fellow belongs to a different Team,
    # therefore Team Alpha feedback must not leak.
    assert response.json() is None

    # Cleanup
    db.delete(other_membership)
    db.delete(other_enrollment)
    db.delete(other_fellow)
    db.delete(other_team)

    db.commit()
def test_locked_session_feedback_is_forbidden(
    db: Session,
    student_client: TestClient,
    setup_submission_team,
):
    lead = setup_submission_team["lead"]
    team = setup_submission_team["team"]
    cohort = setup_submission_team["cohort"]

    session = (
        db.query(DBSession)
        .filter(
            DBSession.cohort_id == cohort.id,
            DBSession.session_number == 3,
        )
        .one()
    )

    session.is_unlocked = False
    session.unlock_at = None

    # Create protected data directly in DB.
    # We intentionally bypass Fellow submission
    # because a locked Session would reject it.
    submission = TeamSubmission(
        session_id=session.id,
        team_id=team.id,
        submitted_by_user_id=lead.id,
        drive_url=(
            "https://drive.google.com/"
            "locked-submission"
        ),
    )

    db.add(submission)
    db.flush()

    feedback = SubmissionFeedback(
        submission_id=submission.id,
        reviewed_by_user_id=None,
        feedback_text=(
            "SECRET LOCKED FEEDBACK"
        ),
        feedback_url=None,
        status="revision_required",
    )

    db.add(feedback)
    db.commit()

    token = create_access_token(
        subject=str(lead.id),
        role=lead.role.value,
    )

    response = student_client.get(
        (
            f"/api/v1/fellow/sessions/"
            f"{session.id}/feedback"
        ),
        headers={
            "Authorization": f"Bearer {token}"
        },
    )

    assert response.status_code == 403

    assert (
        "locked"
        in response.json()["detail"].lower()
    )

    db.delete(feedback)
    db.delete(submission)
    db.commit()

def test_fellow_can_view_own_company_challenge(
    student_client: TestClient,
    fellow_user: User,
    setup_submission_team,
    db: Session,
):
    team = setup_submission_team["team"]

    team.company_name = "Cikitsa"
    team.company_overview = (
        "Healthcare company focused on patient outcomes."
    )
    team.company_challenge = (
        "Improve patient engagement"
    )
    team.challenge_description = (
        "Explore ways to improve patient engagement "
        "through a stronger service experience."
    )

    resource_a = TeamChallengeResource(
        team_id=team.id,
        title="Company Brief",
        resource_type="link",
        url="https://drive.google.com/file/d/company-brief/view",
        is_downloadable=True,
        sequence=2,
    )

    resource_b = TeamChallengeResource(
        team_id=team.id,
        title="Industry Brief",
        resource_type="link",
        url="https://docs.google.com/document/d/industry-brief",
        is_downloadable=False,
        sequence=1,
    )

    db.add_all(
        [
            resource_a,
            resource_b,
        ]
    )
    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    response = student_client.get(
        "/api/v1/fellow/company-challenge",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["team_id"] == str(team.id)
    assert data["team_name"] == team.name

    assert data["company_name"] == "Cikitsa"
    assert (
        data["company_overview"]
        == "Healthcare company focused on patient outcomes."
    )

    assert (
        data["company_challenge"]
        == "Improve patient engagement"
    )

    assert len(data["resources"]) == 2

    assert [
        item["title"]
        for item in data["resources"]
    ] == [
        "Industry Brief",
        "Company Brief",
    ]


def test_fellow_company_challenge_isolated_by_team(
    student_client: TestClient,
    fellow_user: User,
    setup_submission_team,
    db: Session,
):
    own_team = setup_submission_team["team"]
    cohort = setup_submission_team["cohort"]

    own_team.company_name = "Own Company"
    own_team.company_challenge = "Own Challenge"

    own_resource = TeamChallengeResource(
        team_id=own_team.id,
        title="Own Team Brief",
        resource_type="link",
        url="https://drive.google.com/file/d/own-team/view",
        is_downloadable=False,
        sequence=1,
    )

    other_team = Team(
        cohort_id=cohort.id,
        name=f"Other Team {uuid4().hex[:8]}",
        company_name="Private Company",
        company_challenge="Private Challenge",
        is_active=True,
    )

    db.add(other_team)
    db.flush()

    other_resource = TeamChallengeResource(
        team_id=other_team.id,
        title="Private Team Brief",
        resource_type="link",
        url="https://drive.google.com/file/d/private-team/view",
        is_downloadable=False,
        sequence=1,
    )

    db.add_all(
        [
            own_resource,
            other_resource,
        ]
    )
    db.commit()

    token = create_access_token(
        subject=str(fellow_user.id),
        role=fellow_user.role.value,
    )

    try:
        response = student_client.get(
            (
                "/api/v1/fellow/company-challenge"
                f"?team_id={other_team.id}"
            ),
            headers={
                "Authorization": f"Bearer {token}",
            },
        )

        assert response.status_code == 200

        data = response.json()

        assert data["team_id"] == str(own_team.id)
        assert data["company_name"] == "Own Company"
        assert (
            data["company_challenge"]
            == "Own Challenge"
        )

        titles = [
            item["title"]
            for item in data["resources"]
        ]

        assert "Own Team Brief" in titles
        assert "Private Team Brief" not in titles

        assert data["company_name"] != "Private Company"

    finally:
        db.delete(other_team)
        db.commit()
