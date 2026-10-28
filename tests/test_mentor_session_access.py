"""
Tests for Mentor Session Access & Join Meeting.
Verifies that:
- Assigned mentors can view team sessions and session detail.
- Meeting URL is shared from the single Admin-configured session record.
- Unassigned mentors receive 403 Forbidden.
- Mismatched cohort sessions return 404.
- Locked sessions return 403.
- When Admin updates meeting URL, Mentor immediately sees the update without separate records.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models.checklist import MentorTeamAssignment
from app.models.cohort import Cohort, CohortStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionType, SessionStatus
from app.models.team import Team
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week


def auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mentor_session_setup(db):
    suffix = uuid4().hex[:8]
    program = Program(name=f"MS Program {suffix}", code=f"MSP-{suffix}")
    db.add(program)
    db.flush()

    phase = Phase(
        program_id=program.id,
        code="DISCOVER",
        name="Discover Phase",
        development_role="THINK",
        sequence=1,
        duration_weeks=4,
        is_active=True,
    )
    db.add(phase)
    db.flush()

    week = Week(
        phase_id=phase.id,
        week_number=1,
        title="Context & Framing",
        sequence=1,
    )
    cohort_1 = Cohort(
        program_id=program.id,
        name=f"Batch 01 {suffix}",
        code=f"B01-{suffix}",
        start_date=date.today(),
        status=CohortStatus.ACTIVE,
    )
    cohort_2 = Cohort(
        program_id=program.id,
        name=f"Batch 02 {suffix}",
        code=f"B02-{suffix}",
        start_date=date.today(),
        status=CohortStatus.ACTIVE,
    )
    db.add_all([week, cohort_1, cohort_2])
    db.flush()

    # Session for Cohort 1
    session_1 = DBSession(
        cohort_id=cohort_1.id,
        phase_id=phase.id,
        week_id=week.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Business Context & Evidence",
        description="Understanding market signals and evidence.",
        sequence=1,
        is_unlocked=True,
        start_at=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 1, 11, 30, tzinfo=timezone.utc),
        meeting_url="https://meet.google.com/abc-defg-hij",
        meeting_provider="Google Meet",
    )

    # Locked Session for Cohort 1
    session_locked = DBSession(
        cohort_id=cohort_1.id,
        phase_id=phase.id,
        week_id=week.id,
        session_number=2,
        session_type=SessionType.LEARN_WORK,
        title="Problem Diagnosis",
        sequence=2,
        is_unlocked=False,
        meeting_url="https://meet.google.com/locked-session",
    )

    # Session for Cohort 2
    session_cohort_2 = DBSession(
        cohort_id=cohort_2.id,
        phase_id=phase.id,
        week_id=week.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Cohort 2 Session 1",
        sequence=1,
        is_unlocked=True,
        meeting_url="https://meet.google.com/cohort2-meet",
    )
    db.add_all([session_1, session_locked, session_cohort_2])

    # Teams
    team_alpha = Team(cohort_id=cohort_1.id, name=f"Team Alpha {suffix}", is_active=True)
    team_beta = Team(cohort_id=cohort_2.id, name=f"Team Beta {suffix}", is_active=True)
    db.add_all([team_alpha, team_beta])
    db.flush()

    # Users
    mentor_user = User(
        first_name="Samatha",
        last_name="Reddy",
        email=f"mentor-{suffix}@example.com",
        password_hash=hash_password("MentorPass123!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
    )
    unassigned_mentor = User(
        first_name="Other",
        last_name="Mentor",
        email=f"other-mentor-{suffix}@example.com",
        password_hash=hash_password("MentorPass123!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
    )
    db.add_all([mentor_user, unassigned_mentor])
    db.flush()

    # Assign mentor to Team Alpha only
    assignment = MentorTeamAssignment(
        mentor_user_id=mentor_user.id,
        team_id=team_alpha.id,
        cohort_id=cohort_1.id,
    )
    db.add(assignment)
    db.commit()

    return {
        "cohort_1": cohort_1,
        "cohort_2": cohort_2,
        "team_alpha": team_alpha,
        "team_beta": team_beta,
        "mentor_user": mentor_user,
        "unassigned_mentor": unassigned_mentor,
        "session_1": session_1,
        "session_locked": session_locked,
        "session_cohort_2": session_cohort_2,
    }


def test_mentor_list_team_sessions_success(client, db, mentor_session_setup):
    s = mentor_session_setup
    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions",
        headers=auth_headers(s["mentor_user"]),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["team_id"] == str(s["team_alpha"].id)
    assert data["team_name"] == s["team_alpha"].name
    assert "sessions" in data
    assert len(data["sessions"]) >= 2

    # Check session 1 detail in list
    s1 = next(item for item in data["sessions"] if item["id"] == str(s["session_1"].id))
    assert s1["session_number"] == 1
    assert s1["title"] == "Business Context & Evidence"
    assert s1["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert s1["start_at"] is not None
    assert s1["status"] is not None


def test_mentor_get_session_detail_success(client, db, mentor_session_setup):
    s = mentor_session_setup
    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions/{s['session_1'].id}",
        headers=auth_headers(s["mentor_user"]),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(s["session_1"].id)
    assert data["team_id"] == str(s["team_alpha"].id)
    assert data["team_name"] == s["team_alpha"].name
    assert data["session_number"] == 1
    assert data["title"] == "Business Context & Evidence"
    assert data["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert data["meeting_provider"] == "Google Meet"
    assert data["is_unlocked"] is True
    assert data["cohort_name"] == s["cohort_1"].name
    assert data["phase_name"] == "Discover Phase"


def test_mentor_unassigned_team_forbidden(client, db, mentor_session_setup):
    s = mentor_session_setup
    # unassigned_mentor tries to access Team Alpha
    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions/{s['session_1'].id}",
        headers=auth_headers(s["unassigned_mentor"]),
    )
    assert res.status_code == 403

    res_list = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions",
        headers=auth_headers(s["unassigned_mentor"]),
    )
    assert res_list.status_code == 403


def test_mentor_session_mismatched_cohort_not_found(client, db, mentor_session_setup):
    s = mentor_session_setup
    # Mentor tries to query Cohort 2's session under Team Alpha (which belongs to Cohort 1)
    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions/{s['session_cohort_2'].id}",
        headers=auth_headers(s["mentor_user"]),
    )
    assert res.status_code == 404
    assert "Session not found for this team's cohort" in res.json()["detail"]


def test_mentor_locked_session_accessible_without_meeting_url(client, db, mentor_session_setup):
    s = mentor_session_setup
    # Mentor queries locked session - should return 200 with details but suppress meeting_url
    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions/{s['session_locked'].id}",
        headers=auth_headers(s["mentor_user"]),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(s["session_locked"].id)
    assert data["is_unlocked"] is False
    assert data["meeting_url"] is None


def test_admin_updates_meeting_url_mentor_sees_update(client, db, mentor_session_setup):
    s = mentor_session_setup
    session = db.get(DBSession, s["session_1"].id)
    session.meeting_url = "https://meet.google.com/updated-meeting-link"
    db.commit()

    res = client.get(
        f"/api/v1/mentor/teams/{s['team_alpha'].id}/sessions/{s['session_1'].id}",
        headers=auth_headers(s["mentor_user"]),
    )
    assert res.status_code == 200
    assert res.json()["meeting_url"] == "https://meet.google.com/updated-meeting-link"
