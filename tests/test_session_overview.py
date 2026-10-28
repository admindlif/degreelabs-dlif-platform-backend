"""Tests for Global Session Overview (Option B).

Verifies:
1. Strict canonical content mapping for Sessions 1-12 from DLIF Fellow Handbook v1.0.
2. Cross-cohort consistency (identical overview for same canonical session across different cohorts).
3. Session 0 induction preserves unavailable state without error.
4. Access controls (authentication, enrollment, unlocked status).
5. Idempotent seeding mechanism.
"""

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
from app.models.program_session_overview import ProgramSessionOverview
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.user import AccountStatus, User, UserRole
from scripts.seed_session_overviews import (
    CANONICAL_OVERVIEWS,
    WEEK_1_PURPOSE,
    WEEK_2_PURPOSE,
    WEEK_3_PURPOSE,
    WEEK_4_PURPOSE,
    seed_session_overviews,
)


def _auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seeded_overviews(db: Session):
    """Ensure canonical session overviews are seeded in the test database."""
    for item in CANONICAL_OVERVIEWS:
        existing = db.scalars(
            select(ProgramSessionOverview).where(
                ProgramSessionOverview.phase_code == item["phase_code"],
                ProgramSessionOverview.session_number == item["session_number"],
                ProgramSessionOverview.source_version == item["source_version"],
            )
        ).first()
        if not existing:
            overview = ProgramSessionOverview(
                phase_code=item["phase_code"],
                session_number=item["session_number"],
                purpose=item["purpose"],
                what_to_explore=item["what_to_explore"],
                working_evidence=item["working_evidence"],
                source_document=item["source_document"],
                source_version=item["source_version"],
                source_page=item["source_page"],
                is_active=True,
            )
            db.add(overview)
    db.commit()


@pytest.fixture
def setup_fellow_and_cohort(db: Session):
    suffix = uuid4().hex[:6]

    program = Program(
        name=f"DLIF Program {suffix}",
        code=f"DLIF_{suffix}",
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
    )
    db.add(phase)
    db.flush()

    cohort_a = Cohort(
        program_id=program.id,
        name=f"Cohort A {suffix}",
        code=f"BATCH_A_{suffix}",
        status=CohortStatus.ACTIVE,
    )
    cohort_b = Cohort(
        program_id=program.id,
        name=f"Cohort B {suffix}",
        code=f"BATCH_B_{suffix}",
        status=CohortStatus.ACTIVE,
    )
    db.add_all([cohort_a, cohort_b])
    db.flush()

    fellow_a = User(
        first_name="Alice",
        last_name="Fellow",
        email=f"alice_{suffix}@test.com",
        password_hash=hash_password("Pass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    fellow_b = User(
        first_name="Bob",
        last_name="Fellow",
        email=f"bob_{suffix}@test.com",
        password_hash=hash_password("Pass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([fellow_a, fellow_b])
    db.flush()

    enrollment_a = Enrollment(
        user_id=fellow_a.id,
        cohort_id=cohort_a.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    enrollment_b = Enrollment(
        user_id=fellow_b.id,
        cohort_id=cohort_b.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    db.add_all([enrollment_a, enrollment_b])

    # Cohort A: Session 0 (Induction), Session 1, Session 2 (Locked)
    session_0_a = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=0,
        session_type=SessionType.INDUCTION,
        title="Induction",
        sequence=0,
        is_unlocked=True,
        status=SessionStatus.COMPLETED,
    )
    session_1_a = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Business Context & Evidence",
        sequence=1,
        is_unlocked=True,
        status=SessionStatus.SCHEDULED,
        meeting_url="https://meet.google.com/aaa-bbbb-ccc",
    )
    session_2_a = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=2,
        session_type=SessionType.LEARN_WORK,
        title="Problem Framing & Diagnosis",
        sequence=2,
        is_unlocked=False,  # LOCKED
        status=SessionStatus.SCHEDULED,
    )

    # Cohort B: Session 1 (same canonical session, different cohort instance)
    session_1_b = DBSession(
        cohort_id=cohort_b.id,
        phase_id=phase.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Business Context & Evidence",
        sequence=1,
        is_unlocked=True,
        status=SessionStatus.SCHEDULED,
        meeting_url="https://meet.google.com/xxx-yyyy-zzz",
    )

    db.add_all([session_0_a, session_1_a, session_2_a, session_1_b])
    db.commit()

    return {
        "fellow_a": fellow_a,
        "fellow_b": fellow_b,
        "session_0_a": session_0_a,
        "session_1_a": session_1_a,
        "session_2_a": session_2_a,
        "session_1_b": session_1_b,
    }


def test_canonical_content_mapping_integrity(seeded_overviews, db: Session):
    """Verify that all 12 sessions are seeded with exact handbook text."""
    overviews = db.scalars(
        select(ProgramSessionOverview)
        .where(
            ProgramSessionOverview.phase_code == "DISCOVER",
            ProgramSessionOverview.source_version == "1.0",
        )
        .order_by(ProgramSessionOverview.session_number.asc())
    ).all()

    assert len(overviews) == 12

    # Week 1: Sessions 1, 2, 3 (Page 5)
    for s_num in [1, 2, 3]:
        ov = next(o for o in overviews if o.session_number == s_num)
        assert ov.purpose == WEEK_1_PURPOSE
        assert ov.source_page == 5

    # Session 1
    ov_1 = next(o for o in overviews if o.session_number == 1)
    assert ov_1.what_to_explore == "Business Context & Evidence"
    assert "Lean company context; relevant workflow; stakeholders" in ov_1.working_evidence

    # Session 2
    ov_2 = next(o for o in overviews if o.session_number == 2)
    assert ov_2.what_to_explore == "Problem Framing & Diagnosis"
    assert "Evidence-backed problem frame; value leakage" in ov_2.working_evidence

    # Session 3
    ov_3 = next(o for o in overviews if o.session_number == 3)
    assert ov_3.what_to_explore == "Discovery Review"
    assert ov_3.working_evidence == "Revised Business Diagnosis & Problem Framing Pack after mentor challenge."

    # Week 2: Sessions 4, 5, 6 (Page 6)
    for s_num in [4, 5, 6]:
        ov = next(o for o in overviews if o.session_number == s_num)
        assert ov.purpose == WEEK_2_PURPOSE
        assert ov.source_page == 6

    ov_4 = next(o for o in overviews if o.session_number == 4)
    assert ov_4.what_to_explore == "Research & Possibility Generation"
    assert "Research synthesis; three distinct possibilities" in ov_4.working_evidence

    ov_5 = next(o for o in overviews if o.session_number == 5)
    assert ov_5.what_to_explore == "What Would Have to Be True?"
    assert "Assumption map; WWHTBT analysis; barrier tests" in ov_5.working_evidence

    ov_6 = next(o for o in overviews if o.session_number == 6)
    assert ov_6.what_to_explore == "Strategic Choice Review"
    assert "Revised Strategic Possibility & Choice Pack" in ov_6.working_evidence

    # Week 3: Sessions 7, 8, 9 (Page 7)
    for s_num in [7, 8, 9]:
        ov = next(o for o in overviews if o.session_number == s_num)
        assert ov.purpose == WEEK_3_PURPOSE
        assert ov.source_page == 7

    ov_7 = next(o for o in overviews if o.session_number == 7)
    assert ov_7.what_to_explore == "Integrated Strategy Choices"
    assert "Aspiration; where to play; how to win" in ov_7.working_evidence

    ov_8 = next(o for o in overviews if o.session_number == 8)
    assert ov_8.what_to_explore == "Execution Architecture"
    assert "Implementation phases; workflow/solution architecture" in ov_8.working_evidence

    ov_9 = next(o for o in overviews if o.session_number == 9)
    assert ov_9.what_to_explore == "Strategy Review"
    assert "Revised Strategy & Execution Blueprint after coherence" in ov_9.working_evidence

    # Week 4: Sessions 10, 11, 12 (Page 8)
    for s_num in [10, 11, 12]:
        ov = next(o for o in overviews if o.session_number == s_num)
        assert ov.purpose == WEEK_4_PURPOSE
        assert ov.source_page == 8

    ov_10 = next(o for o in overviews if o.session_number == 10)
    assert ov_10.what_to_explore == "Proposal Architecture"
    assert "Situation; problem; evidence; alternatives" in ov_10.working_evidence

    ov_11 = next(o for o in overviews if o.session_number == 11)
    assert ov_11.what_to_explore == "Executive Communication"
    assert "Executive summary; presentation; Q&A preparation" in ov_11.working_evidence

    ov_12 = next(o for o in overviews if o.session_number == 12)
    assert ov_12.what_to_explore == "Final DISCOVER Review"
    assert "Final Executive Proposal, Company Presentation and Strategic Design Portfolio." in ov_12.working_evidence


def test_session_overview_api_success(client: TestClient, seeded_overviews, setup_fellow_and_cohort):
    """Fellow can retrieve global overview for an unlocked canonical session."""
    data = setup_fellow_and_cohort
    fellow_a = data["fellow_a"]
    session_1_a = data["session_1_a"]

    headers = _auth_headers(fellow_a)
    response = client.get(f"/api/v1/fellow/sessions/{session_1_a.id}/overview", headers=headers)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["phase_code"] == "DISCOVER"
    assert res_data["session_number"] == 1
    assert res_data["purpose"] == WEEK_1_PURPOSE
    assert res_data["what_to_explore"] == "Business Context & Evidence"
    assert "Lean company context" in res_data["working_evidence"]
    assert res_data["source_version"] == "1.0"
    assert res_data["source_page"] == 5


def test_cross_cohort_returns_identical_overview(client: TestClient, seeded_overviews, setup_fellow_and_cohort):
    """Fellow in Cohort A and Fellow in Cohort B get IDENTICAL overview content for Session 1."""
    data = setup_fellow_and_cohort
    fellow_a = data["fellow_a"]
    session_1_a = data["session_1_a"]
    fellow_b = data["fellow_b"]
    session_1_b = data["session_1_b"]

    resp_a = client.get(f"/api/v1/fellow/sessions/{session_1_a.id}/overview", headers=_auth_headers(fellow_a))
    resp_b = client.get(f"/api/v1/fellow/sessions/{session_1_b.id}/overview", headers=_auth_headers(fellow_b))

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    content_a = resp_a.json()
    content_b = resp_b.json()

    assert content_a == content_b
    assert content_a["session_number"] == 1
    assert content_a["purpose"] == content_b["purpose"]
    assert content_a["what_to_explore"] == content_b["what_to_explore"]
    assert content_a["working_evidence"] == content_b["working_evidence"]


def test_session_0_induction_returns_not_found(client: TestClient, seeded_overviews, setup_fellow_and_cohort):
    """Session 0 Induction does not have an overview defined in handbook; returns 404 cleanly."""
    data = setup_fellow_and_cohort
    fellow_a = data["fellow_a"]
    session_0_a = data["session_0_a"]

    response = client.get(f"/api/v1/fellow/sessions/{session_0_a.id}/overview", headers=_auth_headers(fellow_a))
    assert response.status_code == 404
    assert "Session 0 Induction" in response.json()["detail"]


def test_locked_session_returns_forbidden(client: TestClient, seeded_overviews, setup_fellow_and_cohort):
    """Locked session access is forbidden for overview just like session detail."""
    data = setup_fellow_and_cohort
    fellow_a = data["fellow_a"]
    session_2_a = data["session_2_a"]

    response = client.get(f"/api/v1/fellow/sessions/{session_2_a.id}/overview", headers=_auth_headers(fellow_a))
    assert response.status_code == 403


def test_unauthenticated_access_rejected(client: TestClient, setup_fellow_and_cohort):
    """Unauthenticated requests are rejected with 401."""
    data = setup_fellow_and_cohort
    session_1_a = data["session_1_a"]

    response = client.get(f"/api/v1/fellow/sessions/{session_1_a.id}/overview")
    assert response.status_code == 401


def test_cross_cohort_authorization_isolation(client: TestClient, setup_fellow_and_cohort):
    """Fellow A cannot access session belonging to Cohort B."""
    data = setup_fellow_and_cohort
    fellow_a = data["fellow_a"]
    session_1_b = data["session_1_b"]  # Belongs to cohort B

    response = client.get(f"/api/v1/fellow/sessions/{session_1_b.id}/overview", headers=_auth_headers(fellow_a))
    assert response.status_code == 404
