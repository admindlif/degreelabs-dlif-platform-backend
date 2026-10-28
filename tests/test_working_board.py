"""
Tests for Working Board and Working Board Insights (Functions 1-8).
"""

from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.feedback import SubmissionFeedback
from app.models.phase import Phase
from app.models.program import Program
from app.models.progression import TeamPhaseProgression
from app.models.session import Session as DBSession, SessionType
from app.models.submission import TeamSubmission
from app.models.team import Team, TeamMembership, TeamMemberRole
from app.models.user import AccountStatus, User, UserRole
from app.models.week import Week
from app.models.working_board import (
    WorkingBoard,
    WorkingBoardInsightRevision,
    WorkingBoardInsights,
)
from app.models.checklist import (
    ChecklistItem,
    ChecklistReviewEvent,
    FellowChecklistCompletion,
    MentorTeamAssignment,
)
from app.models.notification import PortalNotification


def auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


def test_checklist_my_thoughts_round_trip(client, db, wb_setup):
    """Thoughts persist, remain private to the fellow, and reopen review on edit."""
    data = wb_setup
    item = ChecklistItem(
        cohort_id=data["cohort"].id,
        session_id=data["session"].id,
        title="Thoughts response test",
        is_active=True,
    )
    db.add(item)
    db.commit()
    response_url = f"/api/v1/fellow/checklist/{item.id}/response"
    mentor_url = (
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}"
        f"/fellows/{data['fellow_a'].id}"
    )
    fellow_headers = auth_headers(data["fellow_a"])
    mentor_headers = auth_headers(data["assigned_mentor"])
    response = {"contribution": "My work", "reflection": "My learning"}
    saved = client.put(
        response_url,
        headers=fellow_headers,
        json={**response, "my_thoughts": "An idea\nA question"},
    )
    assert saved.status_code == 200
    assert saved.json()["my_thoughts"] == "An idea\nA question"
    assert saved.json()["review_status"] == "pending_review"
    listed = client.get("/api/v1/fellow/checklist", headers=fellow_headers).json()
    assert next(row for row in listed["items"] if row["id"] == str(item.id))["my_thoughts"] == "An idea\nA question"
    assert client.get(mentor_url, headers=mentor_headers).json()["my_thoughts"] == "An idea\nA question"
    other_fellow_items = client.get(
        "/api/v1/fellow/checklist", headers=auth_headers(data["fellow_b"])
    ).json()["items"]
    assert next(row for row in other_fellow_items if row["id"] == str(item.id))["my_thoughts"] is None
    with patch("app.services.mentor_checklist.send_email"):
        assert client.post(
            f"{mentor_url}/review", headers=mentor_headers, json={"action": "accept"}
        ).status_code == 200
    edited = client.put(
        response_url, headers=fellow_headers,
        json={**response, "my_thoughts": "A revised idea"},
    )
    assert edited.json()["review_status"] == "pending_review"
    assert edited.json()["my_thoughts"] == "A revised idea"
    # Older clients omit the optional field; they must preserve saved thoughts.
    legacy = client.put(response_url, headers=fellow_headers, json=response)
    assert legacy.status_code == 200
    assert legacy.json()["my_thoughts"] == "A revised idea"
    cleared = client.put(
        response_url, headers=fellow_headers, json={**response, "my_thoughts": ""}
    )
    assert cleared.json()["my_thoughts"] is None
    assert client.put(
        response_url, headers=fellow_headers,
        json={**response, "my_thoughts": "x" * 10001},
    ).status_code == 422


@pytest.fixture
def wb_setup(db):
    suffix = uuid4().hex[:8]
    program = Program(name=f"WB Program {suffix}", code=f"WBP-{suffix}")
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
        title="Week 1",
        sequence=1,
    )
    cohort = Cohort(
        program_id=program.id,
        name=f"Cohort {suffix}",
        code=f"COH-{suffix}",
        start_date=date.today(),
        status=CohortStatus.ACTIVE,
    )
    db.add_all([week, cohort])
    db.flush()

    session = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        week_id=week.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Working Board Session",
        sequence=1,
        is_unlocked=True,
    )
    db.add(session)

    # Teams
    team_a = Team(cohort_id=cohort.id, name=f"Team A {suffix}", is_active=True)
    team_b = Team(cohort_id=cohort.id, name=f"Team B {suffix}", is_active=True)
    db.add_all([team_a, team_b])
    db.flush()

    # Users
    admin_user = User(
        first_name="Admin",
        last_name="User",
        email=f"admin-{suffix}@example.com",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    fellow_a = User(
        first_name="FellowA",
        last_name="Member",
        email=f"fellow-a-{suffix}@example.com",
        password_hash=hash_password("FellowPass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    fellow_b = User(
        first_name="FellowB",
        last_name="Member",
        email=f"fellow-b-{suffix}@example.com",
        password_hash=hash_password("FellowPass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    assigned_mentor = User(
        first_name="Assigned",
        last_name="Mentor",
        email=f"mentor-assigned-{suffix}@example.com",
        password_hash=hash_password("MentorPass123!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    unassigned_mentor = User(
        first_name="Unassigned",
        last_name="Mentor",
        email=f"mentor-unassigned-{suffix}@example.com",
        password_hash=hash_password("MentorPass123!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([admin_user, fellow_a, fellow_b, assigned_mentor, unassigned_mentor])
    db.flush()

    # Enrollments
    db.add(Enrollment(user_id=fellow_a.id, cohort_id=cohort.id, enrollment_status=EnrollmentStatus.ACTIVE))
    db.add(Enrollment(user_id=fellow_b.id, cohort_id=cohort.id, enrollment_status=EnrollmentStatus.ACTIVE))

    # Team Memberships
    db.add(TeamMembership(user_id=fellow_a.id, team_id=team_a.id, cohort_id=cohort.id, team_role=TeamMemberRole.MEMBER))
    db.add(TeamMembership(user_id=fellow_b.id, team_id=team_b.id, cohort_id=cohort.id, team_role=TeamMemberRole.MEMBER))

    # Mentor assignment: assigned_mentor -> team_a
    db.add(MentorTeamAssignment(mentor_user_id=assigned_mentor.id, team_id=team_a.id, cohort_id=cohort.id))

    db.commit()

    return {
        "admin": admin_user,
        "fellow_a": fellow_a,
        "fellow_b": fellow_b,
        "assigned_mentor": assigned_mentor,
        "unassigned_mentor": unassigned_mentor,
        "cohort": cohort,
        "session": session,
        "team_a": team_a,
        "team_b": team_b,
    }


def test_admin_creates_working_board(client, db, wb_setup):
    """1. Admin creates a Working Board for a team/session."""
    data = wb_setup
    payload = {
        "session_id": str(data["session"].id),
        "team_id": str(data["team_a"].id),
        "title": "Sprint 1 Working Board",
        "working_board_url": "https://docs.google.com/document/d/1234567890/edit",
        "instructions": "Collaborate on stakeholder interview analysis.",
        "is_published": False,
    }
    response = client.post(
        "/api/v1/admin/working-boards",
        json=payload,
        headers=auth_headers(data["admin"]),
    )
    assert response.status_code == 201
    res = response.json()
    assert res["title"] == "Sprint 1 Working Board"
    assert res["working_board_url"] == "https://docs.google.com/document/d/1234567890/edit"
    assert res["instructions"] == "Collaborate on stakeholder interview analysis."
    assert res["is_published"] is False
    assert res["session_id"] == str(data["session"].id)
    assert res["team_id"] == str(data["team_a"].id)


def test_admin_updates_link_and_instructions(client, db, wb_setup):
    """2. Admin updates its link and instructions, and publication state."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Initial Board",
        working_board_url="https://docs.google.com/document/d/initial/edit",
        instructions="Old instructions",
        is_published=False,
    )
    db.add(board)
    db.commit()

    # Update link & instructions
    update_payload = {
        "working_board_url": "https://docs.google.com/document/d/updated/edit",
        "instructions": "Updated instructions for sprint work.",
    }
    resp = client.put(
        f"/api/v1/admin/working-boards/{board.id}",
        json=update_payload,
        headers=auth_headers(data["admin"]),
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["working_board_url"] == "https://docs.google.com/document/d/updated/edit"
    assert res["instructions"] == "Updated instructions for sprint work."

    # Patch publication
    patch_resp = client.patch(
        f"/api/v1/admin/working-boards/{board.id}/publication",
        json={"is_published": True},
        headers=auth_headers(data["admin"]),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_published"] is True


def test_duplicate_working_board_prevented(client, db, wb_setup):
    """3. Duplicate Working Board creation is prevented (409 Conflict)."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Existing Board",
        working_board_url="https://docs.google.com/document/d/existing/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    payload = {
        "session_id": str(data["session"].id),
        "team_id": str(data["team_a"].id),
        "title": "Duplicate Board",
        "working_board_url": "https://docs.google.com/document/d/duplicate/edit",
        "is_published": True,
    }
    response = client.post(
        "/api/v1/admin/working-boards",
        json=payload,
        headers=auth_headers(data["admin"]),
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_fellow_sees_correct_working_board(client, db, wb_setup):
    """4. Fellow sees the correct Working Board."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        instructions="Work together here.",
        is_published=True,
    )
    db.add(board)
    db.commit()

    resp = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/working-board",
        headers=auth_headers(data["fellow_a"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Team A Board"
    assert body["working_board_url"] == "https://docs.google.com/document/d/teama/edit"
    assert body["cta_text"] == "Click here to open the Working Board and work collaboratively."


def test_another_teams_fellow_denied_access(client, db, wb_setup):
    """5. Another team's Fellow is denied access to Team A's board."""
    data = wb_setup
    # Only Team A has a board configured
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    # Fellow B (from Team B) requests working board for this session
    resp = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/working-board",
        headers=auth_headers(data["fellow_b"]),
    )
    assert resp.status_code == 404
    assert "no published working board" in resp.json()["detail"].lower()


def test_assigned_mentor_can_read_board(client, db, wb_setup):
    """6. Assigned Mentor can read the board."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    resp = client.get(
        f"/api/v1/mentor/working-boards/{board.id}",
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(board.id)
    assert resp.json()["title"] == "Team A Board"


def test_unassigned_mentor_cannot_read_board(client, db, wb_setup):
    """7. Unassigned Mentor cannot read it."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    resp = client.get(
        f"/api/v1/mentor/working-boards/{board.id}",
        headers=auth_headers(data["unassigned_mentor"]),
    )
    assert resp.status_code == 403
    assert "not assigned" in resp.json()["detail"].lower()


def test_mentor_saves_draft_insights(client, db, wb_setup):
    """8. Mentor saves draft insights."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    payload = {
        "insights_text": "Draft notes:\nFellow A: good start on market study.\nTeam: align on metrics.",
        "publication_status": "draft",
    }
    resp = client.put(
        f"/api/v1/mentor/working-boards/{board.id}/insights",
        json=payload,
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["publication_status"] == "draft"
    assert "Draft notes:" in body["insights_text"]
    assert body["published_at"] is None


def test_draft_insights_hidden_from_fellows(client, db, wb_setup):
    """9. Draft insights are hidden from fellows."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.flush()

    insight = WorkingBoardInsights(
        working_board_id=board.id,
        mentor_user_id=data["assigned_mentor"].id,
        insights_text="Confidential draft observations.",
        publication_status="draft",
    )
    db.add(insight)
    db.commit()

    resp = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/working-board-insights",
        headers=auth_headers(data["fellow_a"]),
    )
    assert resp.status_code == 404
    assert "no published working board insights" in resp.json()["detail"].lower()


def test_mentor_publishes_insights(client, db, wb_setup):
    """10. Mentor publishes insights."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.flush()

    insight = WorkingBoardInsights(
        working_board_id=board.id,
        mentor_user_id=data["assigned_mentor"].id,
        insights_text="Draft observations ready to publish.",
        publication_status="draft",
    )
    db.add(insight)
    db.commit()

    with patch("app.services.working_board.send_email") as mock_email:
        resp = client.post(
            f"/api/v1/mentor/working-boards/{board.id}/insights/publish",
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["publication_status"] == "published"
        assert body["published_at"] is not None
        assert mock_email.called


def test_fellow_can_view_published_insights(client, db, wb_setup):
    """11. Fellow can view published insights."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.flush()

    insight = WorkingBoardInsights(
        working_board_id=board.id,
        mentor_user_id=data["assigned_mentor"].id,
        insights_text="Fellow A:\nGreat context analysis.\n\nTeam:\nRefine next steps.",
        publication_status="published",
        published_at=datetime.now(timezone.utc),
    )
    db.add(insight)
    db.commit()

    resp = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/working-board-insights",
        headers=auth_headers(data["fellow_a"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["insights_text"] == "Fellow A:\nGreat context analysis.\n\nTeam:\nRefine next steps."
    assert body["tagline"] == "Insights provided by the Team's Mentor."
    assert body["publication_status"] == "published"


def test_mentor_edits_insights_retaining_audit_history(client, db, wb_setup):
    """12. Mentor edits insights while retaining audit history."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.commit()

    # Step 1: initial draft
    client.put(
        f"/api/v1/mentor/working-boards/{board.id}/insights",
        json={"insights_text": "Revision 1 text", "publication_status": "draft"},
        headers=auth_headers(data["assigned_mentor"]),
    )

    # Step 2: publish
    client.post(
        f"/api/v1/mentor/working-boards/{board.id}/insights/publish",
        headers=auth_headers(data["assigned_mentor"]),
    )

    # Step 3: edit published
    client.put(
        f"/api/v1/mentor/working-boards/{board.id}/insights",
        json={"insights_text": "Revision 3 text (updated after review)", "publication_status": "published"},
        headers=auth_headers(data["assigned_mentor"]),
    )

    revisions = db.scalars(
        select(WorkingBoardInsightRevision)
        .where(WorkingBoardInsightRevision.working_board_id == board.id)
        .order_by(WorkingBoardInsightRevision.revision_number.asc())
    ).all()

    assert len(revisions) == 3
    assert revisions[0].revision_number == 1
    assert revisions[0].insights_text == "Revision 1 text"
    assert revisions[1].revision_number == 2
    assert revisions[1].publication_status == "published"
    assert revisions[2].revision_number == 3
    assert "Revision 3 text" in revisions[2].insights_text


def test_existing_checklist_apis_still_work(client, db, wb_setup):
    """13. Existing checklist and Checklist Feedback APIs still work."""
    from app.models.checklist import ChecklistItem, FellowChecklistCompletion
    data = wb_setup

    item = ChecklistItem(
        cohort_id=data["cohort"].id,
        phase_id=data["session"].phase_id,
        week_id=data["session"].week_id,
        session_id=data["session"].id,
        title="Deliverable Checklist Item",
        is_required=True,
        is_active=True,
        sequence=1,
    )
    db.add(item)
    db.commit()

    # Fellow submits response for item
    complete_resp = client.put(
        f"/api/v1/fellow/checklist/{item.id}/response",
        json={
            "contribution": "Wrote user journey",
            "reflection": "Learned interview techniques",
        },
        headers=auth_headers(data["fellow_a"]),
    )
    assert complete_resp.status_code == 200

    # Mentor reviews item
    review_resp = client.post(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/review",
        json={"action": "accept"},
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["review_status"] == "accepted"


def test_historical_submissions_and_feedback_remain_intact(client, db, wb_setup):
    """14. Existing historical submissions and feedback remain intact."""
    data = wb_setup

    sub = TeamSubmission(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        submitted_by_user_id=data["fellow_a"].id,
        drive_url="https://docs.google.com/document/d/legacy/edit",
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(sub)
    db.flush()

    fb = SubmissionFeedback(
        submission_id=sub.id,
        reviewed_by_user_id=data["assigned_mentor"].id,
        feedback_text="Legacy feedback intact.",
        status="accepted",
    )
    db.add(fb)
    db.commit()

    # Legacy Fellow GET submission
    resp_sub = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/submission",
        headers=auth_headers(data["fellow_a"]),
    )
    assert resp_sub.status_code == 200
    assert resp_sub.json()["submission"]["drive_url"] == "https://docs.google.com/document/d/legacy/edit"

    # Legacy Fellow GET feedback
    resp_fb = client.get(
        f"/api/v1/fellow/sessions/{data['session'].id}/feedback",
        headers=auth_headers(data["fellow_a"]),
    )
    assert resp_fb.status_code == 200
    assert resp_fb.json()["feedback_text"] == "Legacy feedback intact."


def test_database_upgrade_and_rollback_safety(db):
    """15. Database models and schema integrity."""
    # Ensure working_boards, working_board_insights, working_board_insight_revisions tables exist
    assert WorkingBoard.__tablename__ == "working_boards"
    assert WorkingBoardInsights.__tablename__ == "working_board_insights"
    assert WorkingBoardInsightRevision.__tablename__ == "working_board_insight_revisions"


def test_publishing_insights_triggers_notification_without_duplication(client, db, wb_setup):
    """16. Publishing insights triggers correct notification without duplicate sends on retry."""
    data = wb_setup
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Team A Board",
        working_board_url="https://docs.google.com/document/d/teama/edit",
        is_published=True,
    )
    db.add(board)
    db.flush()

    insight = WorkingBoardInsights(
        working_board_id=board.id,
        mentor_user_id=data["assigned_mentor"].id,
        insights_text="Final team insights ready for review.",
        publication_status="draft",
    )
    db.add(insight)
    db.commit()

    with patch("app.services.working_board.send_email") as mock_email:
        # First publish: should send notification
        resp1 = client.post(
            f"/api/v1/mentor/working-boards/{board.id}/insights/publish",
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert resp1.status_code == 200
        assert mock_email.call_count == 1

        # Second publish (retry): should NOT send duplicate email
        resp2 = client.post(
            f"/api/v1/mentor/working-boards/{board.id}/insights/publish",
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert resp2.status_code == 200
        assert mock_email.call_count == 1  # count remains 1, no duplicate sent


def test_multi_fellow_checklist_review_lifecycle_and_working_board(client, db, wb_setup):
    """Rows are isolated per fellow and collaborative work is mentor-reviewed."""
    data = wb_setup
    suffix = uuid4().hex[:8]
    second_fellow = User(
        first_name="Second",
        last_name="Teammate",
        email=f"second-fellow-{suffix}@example.com",
        password_hash=hash_password("FellowPass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(second_fellow)
    db.flush()
    db.add_all(
        [
            Enrollment(
                user_id=second_fellow.id,
                cohort_id=data["cohort"].id,
                enrollment_status=EnrollmentStatus.ACTIVE,
            ),
            TeamMembership(
                user_id=second_fellow.id,
                team_id=data["team_a"].id,
                cohort_id=data["cohort"].id,
                team_role=TeamMemberRole.MEMBER,
            ),
        ]
    )
    item = ChecklistItem(
        cohort_id=data["cohort"].id,
        phase_id=data["session"].phase_id,
        week_id=data["session"].week_id,
        session_id=data["session"].id,
        required_submission_session_id=data["session"].id,
        title="Collaborative evidence review",
        is_required=True,
        is_active=True,
        sequence=50,
    )
    board = WorkingBoard(
        session_id=data["session"].id,
        team_id=data["team_a"].id,
        title="Published board is evidence, not completion",
        working_board_url="https://docs.google.com/document/d/checklist-flow/edit",
        is_published=True,
    )
    db.add_all([item, board])
    db.commit()

    # No legacy TeamSubmission exists, and the published board does not mark
    # either fellow complete. Each fellow gets a distinct row identifier.
    overview = client.get(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist",
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert overview.status_code == 200
    rows = [row for row in overview.json() if row["checklist_item_id"] == str(item.id)]
    assert len(rows) == 2
    assert len({row["id"] for row in rows}) == 2
    assert {row["review_status"] for row in rows} == {"not_checked"}

    with patch("app.services.mentor_checklist.send_email"):
        submitted = client.put(
            f"/api/v1/fellow/checklist/{item.id}/response",
            json={"contribution": "Drafted an unfinished section", "reflection": "Need more evidence"},
            headers=auth_headers(data["fellow_a"]),
        )
        assert submitted.status_code == 200
        assert submitted.json()["review_status"] == "pending_review"
        assert submitted.json()["is_completed"] is False

        rejected = client.post(
            f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/review",
            json={"action": "reject", "rejection_reason": "Add interview evidence."},
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert rejected.status_code == 200
        assert rejected.json()["review_status"] == "rejected"
        assert rejected.json()["rejection_reason"] == "Add interview evidence."

        fellow_list = client.get(
            "/api/v1/fellow/checklist", headers=auth_headers(data["fellow_a"])
        ).json()
        rejected_item = next(row for row in fellow_list["items"] if row["id"] == str(item.id))
        assert rejected_item["is_completed"] is False
        assert rejected_item["review_status"] == "rejected"

        resubmitted = client.put(
            f"/api/v1/fellow/checklist/{item.id}/response",
            json={"contribution": "Added interview evidence", "reflection": "Validated the finding"},
            headers=auth_headers(data["fellow_a"]),
        )
        assert resubmitted.status_code == 200
        assert resubmitted.json()["review_status"] == "pending_review"
        assert resubmitted.json()["rejection_reason"] is None

        accepted = client.post(
            f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/review",
            json={"action": "accept"},
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert accepted.status_code == 200
        assert accepted.json()["review_status"] == "accepted"

        # Editing accepted content always opens a fresh review.
        edited = client.put(
            f"/api/v1/fellow/checklist/{item.id}/response",
            json={"contribution": "Refined accepted answer", "reflection": "New learning"},
            headers=auth_headers(data["fellow_a"]),
        )
        assert edited.status_code == 200
        assert edited.json()["review_status"] == "pending_review"

    # The second fellow's row was never mutated by the first fellow's actions.
    overview = client.get(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist",
        headers=auth_headers(data["assigned_mentor"]),
    ).json()
    by_fellow = {
        row["fellow_id"]: row
        for row in overview
        if row["checklist_item_id"] == str(item.id)
    }
    assert by_fellow[str(data["fellow_a"].id)]["review_status"] == "pending_review"
    assert by_fellow[str(second_fellow.id)]["review_status"] == "not_checked"
    assert db.scalar(
        select(TeamSubmission).where(
            TeamSubmission.team_id == data["team_a"].id,
            TeamSubmission.session_id == data["session"].id,
        )
    ) is None
    assert len(
        db.scalars(
            select(ChecklistReviewEvent).where(
                ChecklistReviewEvent.checklist_item_id == item.id,
                ChecklistReviewEvent.fellow_user_id == data["fellow_a"].id,
            )
        ).all()
    ) == 2

    # Team and assignment boundaries are enforced by the canonical endpoint.
    assert client.post(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_b'].id}/review",
        json={"action": "accept"},
        headers=auth_headers(data["assigned_mentor"]),
    ).status_code == 403
    assert client.post(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/review",
        json={"action": "accept"},
        headers=auth_headers(data["unassigned_mentor"]),
    ).status_code == 403


def test_checklist_feedback_and_persistent_mentor_notifications(client, db, wb_setup):
    """Fellow/mentor messages share one authorized thread and notifications persist."""
    data = wb_setup
    item = ChecklistItem(
        cohort_id=data["cohort"].id,
        phase_id=data["session"].phase_id,
        session_id=data["session"].id,
        title="Feedback thread item",
        is_active=True,
        sequence=60,
    )
    db.add(item)
    db.commit()

    with patch("app.services.mentor_checklist.send_email"):
        fellow_message = client.post(
            f"/api/v1/fellow/checklist/{item.id}/feedback",
            json={"message": "Could you review my approach?"},
            headers=auth_headers(data["fellow_a"]),
        )
        assert fellow_message.status_code == 201
        assert fellow_message.json()["author_role"] == "fellow"
        assert fellow_message.json()["author_name"] == "FellowA Member"

        mentor_thread = client.get(
            f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/feedback",
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert mentor_thread.status_code == 200
        assert [message["message"] for message in mentor_thread.json()["messages"]] == [
            "Could you review my approach?"
        ]

        mentor_message = client.post(
            f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/feedback",
            json={"message": "Yes; add one concrete example."},
            headers=auth_headers(data["assigned_mentor"]),
        )
        assert mentor_message.status_code == 201
        assert mentor_message.json()["author_role"] == "mentor"

    fellow_thread = client.get(
        f"/api/v1/fellow/checklist/{item.id}/feedback",
        headers=auth_headers(data["fellow_a"]),
    )
    assert fellow_thread.status_code == 200
    assert [message["author_role"] for message in fellow_thread.json()["messages"]] == [
        "fellow",
        "mentor",
    ]

    assert client.get(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_b'].id}/feedback",
        headers=auth_headers(data["assigned_mentor"]),
    ).status_code == 403
    assert client.get(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist/{item.id}/fellows/{data['fellow_a'].id}/feedback",
        headers=auth_headers(data["unassigned_mentor"]),
    ).status_code == 403

    response = client.get(
        "/api/v1/mentor/notifications",
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_count"] >= 1
    assert payload["unread_count"] >= 1
    notification = payload["notifications"][0]
    assert set(notification) == {
        "id", "type", "title", "message", "is_read", "created_at", "target_url", "meta"
    }

    assert client.post(
        f"/api/v1/mentor/notifications/{notification['id']}/read",
        headers=auth_headers(data["unassigned_mentor"]),
    ).status_code == 404
    assert client.post(
        f"/api/v1/mentor/notifications/{notification['id']}/read",
        headers=auth_headers(data["assigned_mentor"]),
    ).status_code == 204
    assert client.post(
        "/api/v1/mentor/notifications/read-all",
        headers=auth_headers(data["assigned_mentor"]),
    ).status_code == 204
    assert client.get(
        "/api/v1/mentor/notifications",
        headers=auth_headers(data["assigned_mentor"]),
    ).json()["unread_count"] == 0
    assert db.scalar(
        select(PortalNotification).where(
            PortalNotification.recipient_user_id == data["assigned_mentor"].id
        )
    ) is not None


def test_fellow_and_mentor_share_progressed_phase_checklist_scope(client, db, wb_setup):
    """Visibility is shared and follows progression beyond DISCOVER."""
    data = wb_setup
    program_id = data["cohort"].program_id
    validate = Phase(
        program_id=program_id,
        code="VALIDATE",
        name="Validate Phase",
        development_role="PROVE",
        sequence=2,
        duration_weeks=4,
        is_active=True,
    )
    db.add(validate)
    db.flush()
    validate_week = Week(
        phase_id=validate.id,
        week_number=1,
        title="Validation week",
        sequence=1,
    )
    db.add(validate_week)
    db.flush()
    validate_session = DBSession(
        cohort_id=data["cohort"].id,
        phase_id=validate.id,
        week_id=validate_week.id,
        session_number=2,
        session_type=SessionType.LEARN_WORK,
        title="Validation session",
        sequence=2,
    )
    db.add(validate_session)
    db.add(
        TeamPhaseProgression(
            team_id=data["team_a"].id,
            cohort_id=data["cohort"].id,
            phase_code="VALIDATE",
            status="approved",
            decision="shortlisted",
        )
    )
    items = [
        ChecklistItem(
            title="Cohort-wide progressed item",
            cohort_id=data["cohort"].id,
            sequence=70,
            is_active=True,
        ),
        ChecklistItem(
            title="Cohort progressed item",
            cohort_id=data["cohort"].id,
            phase_id=validate.id,
            sequence=71,
            is_active=True,
        ),
        ChecklistItem(
            title="Week progressed item",
            cohort_id=data["cohort"].id,
            week_id=validate_week.id,
            sequence=72,
            is_active=True,
        ),
        ChecklistItem(
            title="Session progressed item",
            cohort_id=data["cohort"].id,
            session_id=validate_session.id,
            sequence=73,
            is_active=True,
        ),
    ]
    db.add_all(items)
    db.commit()

    fellow_response = client.get(
        "/api/v1/fellow/checklist", headers=auth_headers(data["fellow_a"])
    )
    mentor_response = client.get(
        f"/api/v1/mentor/teams/{data['team_a'].id}/checklist",
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert fellow_response.status_code == 200
    assert mentor_response.status_code == 200

    expected_ids = {str(item.id) for item in items}
    fellow_ids = {row["id"] for row in fellow_response.json()["items"]}
    mentor_ids = {
        row["checklist_item_id"]
        for row in mentor_response.json()
        if row["fellow_id"] == str(data["fellow_a"].id)
    }
    assert expected_ids <= fellow_ids
    assert fellow_ids == mentor_ids


def test_mentor_creates_and_manages_team_scoped_checklist_item(client, db, wb_setup):
    """Mentor authoring is team-scoped, creator-owned, and Admin-visible."""
    data = wb_setup
    suffix = uuid4().hex[:8]
    teammate = User(
        first_name="TeamA",
        last_name="SecondFellow",
        email=f"team-a-second-{suffix}@example.com",
        password_hash=hash_password("FellowPass123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(teammate)
    db.flush()
    db.add_all(
        [
            Enrollment(
                user_id=teammate.id,
                cohort_id=data["cohort"].id,
                enrollment_status=EnrollmentStatus.ACTIVE,
            ),
            TeamMembership(
                user_id=teammate.id,
                team_id=data["team_a"].id,
                cohort_id=data["cohort"].id,
                team_role=TeamMemberRole.MEMBER,
            ),
        ]
    )
    db.commit()

    payload = {
        "team_id": str(data["team_a"].id),
        "phase_id": str(data["session"].phase_id),
        "week_id": str(data["session"].week_id),
        "session_id": str(data["session"].id),
        "title": "Complete stakeholder analysis",
        "description": "Update findings in the shared Working Board.",
        "action_url": "https://docs.google.com/document/d/team-a/edit",
        "is_required": True,
        "is_active": True,
        "sequence": 80,
    }
    created = client.post(
        "/api/v1/mentor/checklist-items",
        json=payload,
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    item_id = body["id"]
    assert body["team_id"] == str(data["team_a"].id)
    assert body["created_by_user_id"] == str(data["assigned_mentor"].id)
    assert body["can_edit"] is True

    # Every Fellow on Team A sees one shared definition with independent state.
    for fellow in (data["fellow_a"], teammate):
        fellow_items = client.get(
            "/api/v1/fellow/checklist", headers=auth_headers(fellow)
        ).json()["items"]
        assert item_id in {item["id"] for item in fellow_items}
    other_team_items = client.get(
        "/api/v1/fellow/checklist", headers=auth_headers(data["fellow_b"])
    ).json()["items"]
    assert item_id not in {item["id"] for item in other_team_items}
    assert client.put(
        f"/api/v1/fellow/checklist/{item_id}/response",
        json={"contribution": "Cross-team", "reflection": "Must fail"},
        headers=auth_headers(data["fellow_b"]),
    ).status_code == 404

    # Payload tampering cannot target an unassigned team.
    tampered = {**payload, "team_id": str(data["team_b"].id), "title": "Forbidden"}
    assert client.post(
        "/api/v1/mentor/checklist-items",
        json=tampered,
        headers=auth_headers(data["assigned_mentor"]),
    ).status_code == 403
    assert client.post(
        "/api/v1/mentor/checklist-items",
        json=payload,
        headers=auth_headers(data["unassigned_mentor"]),
    ).status_code == 403

    updated = client.put(
        f"/api/v1/mentor/checklist-items/{item_id}",
        json={"description": "Updated instructions", "title": "Updated stakeholder analysis"},
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated instructions"

    # Even another assigned Mentor cannot change the creator's definition.
    db.add(
        MentorTeamAssignment(
            mentor_user_id=data["unassigned_mentor"].id,
            team_id=data["team_a"].id,
            cohort_id=data["cohort"].id,
        )
    )
    db.commit()
    assert client.put(
        f"/api/v1/mentor/checklist-items/{item_id}",
        json={"title": "Ownership bypass"},
        headers=auth_headers(data["unassigned_mentor"]),
    ).status_code == 403

    # Admin oversight includes and can update Mentor-created definitions.
    admin_items = client.get(
        "/api/v1/admin/checklist-items", headers=auth_headers(data["admin"])
    )
    assert admin_items.status_code == 200
    assert item_id in {item["id"] for item in admin_items.json()}
    admin_update = client.put(
        f"/api/v1/admin/checklist-items/{item_id}",
        json={"due_at": datetime.now(timezone.utc).isoformat()},
        headers=auth_headers(data["admin"]),
    )
    assert admin_update.status_code == 200

    # A response can exist before work is complete; deactivation is soft and
    # preserves the response, creator, review state, and Working Board.
    with patch("app.services.mentor_checklist.send_email"):
        response = client.put(
            f"/api/v1/fellow/checklist/{item_id}/response",
            json={"contribution": "Work in progress", "reflection": "More validation needed"},
            headers=auth_headers(data["fellow_a"]),
        )
    assert response.status_code == 200
    deactivated = client.patch(
        f"/api/v1/mentor/checklist-items/{item_id}/activation",
        json={"is_active": False},
        headers=auth_headers(data["assigned_mentor"]),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    stored_item = db.get(ChecklistItem, UUID(item_id))
    assert stored_item is not None
    assert stored_item.created_by_user_id == data["assigned_mentor"].id
    assert db.scalar(
        select(FellowChecklistCompletion).where(
            FellowChecklistCompletion.checklist_item_id == stored_item.id,
            FellowChecklistCompletion.user_id == data["fellow_a"].id,
        )
    ) is not None
    notifications = db.scalars(
        select(PortalNotification).where(
            PortalNotification.notification_type == "checklist_assigned",
            PortalNotification.notification_metadata["checklist_item_id"].astext == item_id,
        )
    ).all()
    assert {notification.recipient_user_id for notification in notifications} == {
        data["fellow_a"].id,
        teammate.id,
    }
