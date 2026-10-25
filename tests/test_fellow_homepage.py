from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from starlette.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.models.checklist import ChecklistItem, FellowChecklistCompletion
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.feedback import SubmissionFeedback
from app.models.phase import Phase
from app.models.program import Program
from app.models.progression import TeamPhaseProgression
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.submission import TeamSubmission
from app.models.team import Team, TeamMembership, TeamMemberRole
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week


@pytest.fixture
def homepage_setup(db):
    suffix = uuid4().hex[:8]
    program = Program(name=f"DLIF Program {suffix}", code=f"DLIF-{suffix}")
    db.add(program)
    db.flush()

    discover_phase = Phase(
        program_id=program.id,
        code="DISCOVER",
        name="DISCOVER",
        development_role="THINK",
        sequence=1,
        duration_weeks=4,
    )
    validate_phase = Phase(
        program_id=program.id,
        code="VALIDATE",
        name="VALIDATE",
        development_role="PROVE",
        sequence=2,
        duration_weeks=4,
    )
    grow_phase = Phase(
        program_id=program.id,
        code="GROW",
        name="GROW",
        development_role="DELIVER",
        sequence=3,
        duration_weeks=16,
    )
    db.add_all([discover_phase, validate_phase, grow_phase])
    db.flush()

    cohort = Cohort(
        program_id=program.id,
        name=f"Cohort {suffix}",
        code=f"C-{suffix}",
        status=CohortStatus.ACTIVE,
        start_date=date(2026, 1, 1),
    )
    db.add(cohort)
    db.flush()

    week1 = Week(
        phase_id=discover_phase.id,
        week_number=1,
        title="Week 1 Problem Framing",
        sequence=1,
    )
    db.add(week1)
    db.flush()

    session1 = DBSession(
        cohort_id=cohort.id,
        phase_id=discover_phase.id,
        week_id=week1.id,
        session_number=1,
        sequence=1,
        session_type=SessionType.LEARN_WORK,
        title="Session 1: Kickoff",
        is_unlocked=True,
        submission_enabled=True,
    )
    db.add(session1)
    db.flush()

    team = Team(
        cohort_id=cohort.id,
        name=f"Team-{suffix}",
        company_name="Acme Health AI",
        company_challenge="Diagnostic Workflow Acceleration",
    )
    db.add(team)
    db.flush()

    # Create fellow user
    fellow = User(
        first_name="Samantha",
        last_name="Reddy",
        email=f"fellow_{suffix}@degreelabs.com",
        password_hash=hash_password("Pass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    # Create admin user
    admin = User(
        first_name="Admin",
        last_name="User",
        email=f"admin_{suffix}@degreelabs.com",
        password_hash=hash_password("Pass123!"),
        role=UserRole.ADMIN,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([fellow, admin])
    db.flush()

    enrollment = Enrollment(
        user_id=fellow.id,
        cohort_id=cohort.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    membership = TeamMembership(
        team_id=team.id,
        cohort_id=cohort.id,
        user_id=fellow.id,
        team_role=TeamMemberRole.MEMBER,
    )
    db.add_all([enrollment, membership])
    db.commit()

    return {
        "program": program,
        "cohort": cohort,
        "team": team,
        "fellow": fellow,
        "admin": admin,
        "session1": session1,
        "discover_phase": discover_phase,
        "validate_phase": validate_phase,
        "grow_phase": grow_phase,
    }


def test_homepage_new_fellow_default_locks(client, homepage_setup):
    """
    1. Eligible fellow accesses DISCOVER (unlocked).
    2. VALIDATE is locked by default ("Requires company shortlisting after DISCOVER.").
    3. GROW is locked by default ("Requires successful VALIDATE and company approval for paid implementation.").
    """
    fellow = homepage_setup["fellow"]
    token = create_access_token(str(fellow.id), role=fellow.role)

    response = client.get(
        "/api/v1/fellow/homepage",
        cookies={"dlif_access": token},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["fellow"]["display_name"] == "Samantha Reddy"
    assert len(data["phases"]) == 3

    discover = next(p for p in data["phases"] if p["code"] == "DISCOVER")
    assert discover["title"] == "DISCOVER"
    assert discover["principle"] == "THINK"
    assert discover["duration"] == "4 weeks"
    assert discover["access_status"] == "unlocked"
    assert discover["can_enter"] is True
    assert discover["lock_reason"] is None

    validate = next(p for p in data["phases"] if p["code"] == "VALIDATE")
    assert validate["title"] == "VALIDATE"
    assert validate["principle"] == "PROVE"
    assert validate["duration"] == "4 weeks"
    assert validate["access_status"] == "locked"
    assert validate["can_enter"] is False
    assert validate["lock_reason"] == "Requires company shortlisting after DISCOVER."

    grow = next(p for p in data["phases"] if p["code"] == "GROW")
    assert grow["title"] == "GROW"
    assert grow["principle"] == "DELIVER"
    assert grow["duration"] == "Typically 3–6 months"
    assert grow["access_status"] == "locked"
    assert grow["can_enter"] is False
    assert grow["lock_reason"] == "Requires successful VALIDATE and company approval for paid implementation."


def test_discover_completion_does_not_unlock_validate(client, db, homepage_setup):
    """
    CRITICAL BUSINESS RULE:
    Clearing checklist items, uploading submissions, and receiving feedback
    must NOT automatically unlock VALIDATE.
    """
    fellow = homepage_setup["fellow"]
    team = homepage_setup["team"]
    session1 = homepage_setup["session1"]
    cohort = homepage_setup["cohort"]
    discover_phase = homepage_setup["discover_phase"]

    # Add a checklist item and mark as completed
    item = ChecklistItem(
        cohort_id=cohort.id,
        phase_id=discover_phase.id,
        title="Final Discover Presentation",
        is_required=True,
    )
    db.add(item)
    db.flush()

    completion = FellowChecklistCompletion(
        checklist_item_id=item.id,
        user_id=fellow.id,
        is_completed=True,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(completion)

    # Add team submission and feedback
    sub = TeamSubmission(
        session_id=session1.id,
        team_id=team.id,
        submitted_by_user_id=fellow.id,
        drive_url="https://drive.google.com/test-submission",
    )
    db.add(sub)
    db.flush()

    fb = SubmissionFeedback(
        submission_id=sub.id,
        feedback_text="Mastery demonstrated!",
        status="approved",
    )
    db.add(fb)
    db.commit()

    token = create_access_token(str(fellow.id), role=fellow.role)
    response = client.get(
        "/api/v1/fellow/homepage",
        cookies={"dlif_access": token},
    )
    assert response.status_code == 200
    data = response.json()

    validate = next(p for p in data["phases"] if p["code"] == "VALIDATE")
    # MUST still be locked!
    assert validate["can_enter"] is False
    assert validate["access_status"] == "locked"
    assert validate["lock_reason"] == "Requires company shortlisting after DISCOVER."


def test_company_shortlisting_unlocks_validate(client, db, homepage_setup):
    """
    When company shortlisting is recorded, VALIDATE unlocks.
    GROW remains locked until paid implementation approval.
    """
    fellow = homepage_setup["fellow"]
    admin = homepage_setup["admin"]
    team = homepage_setup["team"]
    cohort = homepage_setup["cohort"]

    # Record company shortlisting approval
    admin_token = create_access_token(str(admin.id), role=admin.role)
    res_admin = client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={
            "phase_code": "VALIDATE",
            "status": "approved",
            "decision": "shortlisted",
            "notes": "Acme Health selected team for Phase 2 validation POC.",
        },
        cookies={"dlif_access": admin_token},
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["status"] == "approved"
    assert res_admin.json()["decision"] == "shortlisted"

    # Fellow checks homepage
    fellow_token = create_access_token(str(fellow.id), role=fellow.role)
    response = client.get(
        "/api/v1/fellow/homepage",
        cookies={"dlif_access": fellow_token},
    )
    assert response.status_code == 200
    data = response.json()

    validate = next(p for p in data["phases"] if p["code"] == "VALIDATE")
    assert validate["can_enter"] is True
    assert validate["access_status"] == "unlocked"
    assert validate["lock_reason"] is None

    # GROW is still locked
    grow = next(p for p in data["phases"] if p["code"] == "GROW")
    assert grow["can_enter"] is False
    assert grow["access_status"] == "locked"


def test_paid_implementation_approval_unlocks_grow(client, homepage_setup):
    """
    GROW unlocks only after VALIDATE is unlocked and company approves paid implementation.
    """
    fellow = homepage_setup["fellow"]
    admin = homepage_setup["admin"]
    team = homepage_setup["team"]

    admin_token = create_access_token(str(admin.id), role=admin.role)

    # 1. Approve VALIDATE
    client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "approved", "decision": "shortlisted"},
        cookies={"dlif_access": admin_token},
    )

    # 2. Approve GROW
    client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={
            "phase_code": "GROW",
            "status": "approved",
            "decision": "paid_implementation_approved",
            "notes": "Contract signed for 6-month paid pilot.",
        },
        cookies={"dlif_access": admin_token},
    )

    # Fellow checks homepage
    fellow_token = create_access_token(str(fellow.id), role=fellow.role)
    response = client.get(
        "/api/v1/fellow/homepage",
        cookies={"dlif_access": fellow_token},
    )
    assert response.status_code == 200
    data = response.json()

    validate = next(p for p in data["phases"] if p["code"] == "VALIDATE")
    grow = next(p for p in data["phases"] if p["code"] == "GROW")

    assert validate["can_enter"] is True
    assert validate["access_status"] == "unlocked"
    assert grow["can_enter"] is True
    assert grow["access_status"] == "unlocked"
    assert grow["lock_reason"] is None


def test_revoking_approval_locks_phase(client, homepage_setup):
    """
    If company revokes or changes decision, phase access immediately returns to locked.
    """
    fellow = homepage_setup["fellow"]
    admin = homepage_setup["admin"]
    team = homepage_setup["team"]

    admin_token = create_access_token(str(admin.id), role=admin.role)

    # Shortlist
    client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "approved", "decision": "shortlisted"},
        cookies={"dlif_access": admin_token},
    )

    # Revoke
    client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "rejected", "decision": "revoked"},
        cookies={"dlif_access": admin_token},
    )

    fellow_token = create_access_token(str(fellow.id), role=fellow.role)
    response = client.get(
        "/api/v1/fellow/homepage",
        cookies={"dlif_access": fellow_token},
    )
    assert response.status_code == 200
    data = response.json()

    validate = next(p for p in data["phases"] if p["code"] == "VALIDATE")
    assert validate["can_enter"] is False
    assert validate["access_status"] == "locked"


def test_direct_api_access_enforces_phase_locks(client, homepage_setup):
    """
    Direct requests to /api/v1/fellow/validate/overview and /grow/overview
    cannot bypass locks.
    """
    fellow = homepage_setup["fellow"]
    admin = homepage_setup["admin"]
    team = homepage_setup["team"]

    fellow_token = create_access_token(str(fellow.id), role=fellow.role)
    admin_token = create_access_token(str(admin.id), role=admin.role)

    # Initially locked: direct request fails with 403 Forbidden
    res_val_locked = client.get(
        "/api/v1/fellow/validate/overview",
        cookies={"dlif_access": fellow_token},
    )
    assert res_val_locked.status_code == 403
    assert "Requires company shortlisting after DISCOVER" in res_val_locked.json()["detail"]

    res_grow_locked = client.get(
        "/api/v1/fellow/grow/overview",
        cookies={"dlif_access": fellow_token},
    )
    assert res_grow_locked.status_code == 403
    assert "Requires successful VALIDATE and company approval" in res_grow_locked.json()["detail"]

    # Shortlist team
    client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "approved", "decision": "shortlisted"},
        cookies={"dlif_access": admin_token},
    )

    # Now validate overview succeeds!
    res_val_unlocked = client.get(
        "/api/v1/fellow/validate/overview",
        cookies={"dlif_access": fellow_token},
    )
    assert res_val_unlocked.status_code == 200
    assert res_val_unlocked.json()["status"] == "unlocked"

    # GROW is still locked
    assert client.get("/api/v1/fellow/grow/overview", cookies={"dlif_access": fellow_token}).status_code == 403


def test_unauthorized_actor_cannot_modify_progression(client, homepage_setup):
    """
    Fellows or anonymous callers cannot modify progression records.
    """
    fellow = homepage_setup["fellow"]
    team = homepage_setup["team"]

    fellow_token = create_access_token(str(fellow.id), role=fellow.role)

    # Fellow tries to approve themselves
    res = client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "approved", "decision": "shortlisted"},
        cookies={"dlif_access": fellow_token},
    )
    assert res.status_code == 403

    # Anonymous tries to approve
    res_anon = client.put(
        f"/api/v1/admin/teams/{team.id}/phase-progression",
        json={"phase_code": "VALIDATE", "status": "approved", "decision": "shortlisted"},
    )
    assert res_anon.status_code == 401


def test_fellow_homepage_never_accepts_arbitrary_fellow_id(client, homepage_setup):
    """
    Identity is strictly resolved from authenticated session cookie/token.
    Client cannot spoof query params like ?fellow_id=...
    """
    fellow = homepage_setup["fellow"]
    token = create_access_token(str(fellow.id), role=fellow.role)

    fake_id = str(uuid4())
    response = client.get(
        f"/api/v1/fellow/homepage?fellow_id={fake_id}",
        cookies={"dlif_access": token},
    )
    assert response.status_code == 200
    # Must return Samantha Reddy (the authenticated fellow), ignoring fake_id
    assert response.json()["fellow"]["display_name"] == "Samantha Reddy"


def test_existing_discover_endpoints_remain_functional(client, homepage_setup):
    """
    Preserve all existing Discover backend functionality.
    """
    fellow = homepage_setup["fellow"]
    token = create_access_token(str(fellow.id), role=fellow.role)

    # 1. Discover overview
    res_overview = client.get("/api/v1/fellow/discover/overview", cookies={"dlif_access": token})
    assert res_overview.status_code == 200

    # 2. Discover weeks
    res_weeks = client.get("/api/v1/fellow/discover/weeks", cookies={"dlif_access": token})
    assert res_weeks.status_code == 200
    assert len(res_weeks.json()) >= 1

    # 3. Sessions
    res_sessions = client.get("/api/v1/fellow/sessions", cookies={"dlif_access": token})
    assert res_sessions.status_code == 200
    assert len(res_sessions.json()) >= 1

    # 4. Context
    res_context = client.get("/api/v1/fellow/context", cookies={"dlif_access": token})
    assert res_context.status_code == 200
    assert res_context.json()["current_phase"]["code"] == "DISCOVER"
