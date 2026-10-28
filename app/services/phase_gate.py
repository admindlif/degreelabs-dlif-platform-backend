"""
Phase Gate service – DISCOVER → VALIDATE assessment workflow.

DISCOVER_TO_VALIDATE_DIMENSIONS defines the exact 5 rubric dimensions
from the DLIF Fellow Handbook, Page 9.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.phase_gate import (
    DISCOVER_TO_VALIDATE_GATE,
    PhaseGateAssessment,
    PhaseGateDimensionResult,
)
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.repositories.progression import upsert_team_phase_progression
from app.services.email import send_email


# ---------------------------------------------------------------------------
# Handbook Page 9 rubric – exact 5 dimensions
# ---------------------------------------------------------------------------
DISCOVER_TO_VALIDATE_DIMENSIONS: list[dict] = [
    {
        "key": "problem_insight",
        "title": "Problem & Insight",
        "sequence": 1,
        "description": "Team has validated a real, well-scoped problem backed by primary user research.",
    },
    {
        "key": "market_opportunity",
        "title": "Market Size & Opportunity",
        "sequence": 2,
        "description": "Team has estimated an addressable market and articulated a credible opportunity.",
    },
    {
        "key": "solution_concept",
        "title": "Solution & Concept",
        "sequence": 3,
        "description": "Team has a clear concept that directly addresses the identified problem.",
    },
    {
        "key": "stakeholder_engagement",
        "title": "Stakeholder Engagement",
        "sequence": 4,
        "description": "Team has engaged relevant stakeholders and incorporated their feedback.",
    },
    {
        "key": "team_capability",
        "title": "Team Capability & Collaboration",
        "sequence": 5,
        "description": "Team demonstrates effective collaboration and capability to advance to VALIDATE.",
    },
]


def _get_assessment_or_404(
    db: Session, team_id: UUID, gate_code: str
) -> PhaseGateAssessment:
    assessment = db.scalar(
        select(PhaseGateAssessment).where(
            PhaseGateAssessment.team_id == team_id,
            PhaseGateAssessment.phase_gate_code == gate_code,
        )
    )
    if not assessment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Phase gate assessment not found.",
        )
    return assessment


def get_or_create_gate_assessment(
    db: Session,
    team_id: UUID,
    gate_code: str,
    mentor_user_id: UUID,
) -> PhaseGateAssessment:
    """
    Return the existing assessment or create a fresh draft with blank dimensions.
    """
    assessment = db.scalar(
        select(PhaseGateAssessment).where(
            PhaseGateAssessment.team_id == team_id,
            PhaseGateAssessment.phase_gate_code == gate_code,
        )
    )

    if assessment:
        return assessment

    # Determine cohort from team
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found."
        )

    assessment = PhaseGateAssessment(
        team_id=team_id,
        cohort_id=team.cohort_id,
        mentor_user_id=mentor_user_id,
        phase_gate_code=gate_code,
        status="draft",
        total_cleared=0,
        total_uncleared=0,
    )
    db.add(assessment)
    db.flush()  # get the id

    # Seed blank dimension rows from the rubric
    for dim in DISCOVER_TO_VALIDATE_DIMENSIONS:
        db.add(
            PhaseGateDimensionResult(
                assessment_id=assessment.id,
                dimension_key=dim["key"],
                dimension_title=dim["title"],
                sequence=dim["sequence"],
                is_cleared=None,
            )
        )

    db.commit()
    db.refresh(assessment)
    return assessment


def save_gate_assessment_draft(
    db: Session,
    team_id: UUID,
    gate_code: str,
    mentor_user_id: UUID,
    dimension_updates: list[dict],
    mentor_notes: str | None,
) -> PhaseGateAssessment:
    """
    Save or create a draft assessment.  Mentor can call this multiple times.
    Rejects saves if assessment was already submitted.
    """
    assessment = get_or_create_gate_assessment(db, team_id, gate_code, mentor_user_id)

    if assessment.status == "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already submitted. Contact admin to reopen.",
        )

    # Update dimensions
    for upd in dimension_updates:
        dim = db.scalar(
            select(PhaseGateDimensionResult).where(
                PhaseGateDimensionResult.assessment_id == assessment.id,
                PhaseGateDimensionResult.dimension_key == upd["dimension_key"],
            )
        )
        if dim:
            if "is_cleared" in upd:
                dim.is_cleared = upd["is_cleared"]
            if "mentor_comment" in upd:
                dim.mentor_comment = upd.get("mentor_comment")

    if mentor_notes is not None:
        assessment.mentor_notes = mentor_notes

    # Recompute totals
    all_dims = db.scalars(
        select(PhaseGateDimensionResult).where(
            PhaseGateDimensionResult.assessment_id == assessment.id
        )
    ).all()
    assessment.total_cleared = sum(1 for d in all_dims if d.is_cleared is True)
    assessment.total_uncleared = sum(1 for d in all_dims if d.is_cleared is False)

    db.commit()
    db.refresh(assessment)
    return assessment


def submit_gate_assessment(
    db: Session,
    team_id: UUID,
    gate_code: str,
    mentor_user_id: UUID,
    dimension_updates: list[dict],
    mentor_notes: str | None,
) -> PhaseGateAssessment:
    """
    Final submit.  All 5 dimensions must have a Cleared/Uncleared decision.
    Status transitions to 'submitted' (idempotent if already submitted).
    """
    assessment = save_gate_assessment_draft(
        db, team_id, gate_code, mentor_user_id, dimension_updates, mentor_notes
    )

    # Re-fetch dimensions after the save
    all_dims = db.scalars(
        select(PhaseGateDimensionResult).where(
            PhaseGateDimensionResult.assessment_id == assessment.id
        )
    ).all()

    undecided = [d for d in all_dims if d.is_cleared is None]
    if undecided:
        missing = [d.dimension_title for d in undecided]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"All dimensions must have a decision. Pending: {', '.join(missing)}",
        )

    assessment.status = "submitted"
    assessment.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assessment)
    return assessment


def serialize_assessment(assessment: PhaseGateAssessment) -> dict:
    """Convert assessment + dimensions to a JSON-serialisable dict."""
    dims = sorted(assessment.dimension_results, key=lambda d: d.sequence)
    return {
        "id": str(assessment.id),
        "team_id": str(assessment.team_id),
        "cohort_id": str(assessment.cohort_id),
        "mentor_user_id": str(assessment.mentor_user_id) if assessment.mentor_user_id else None,
        "phase_gate_code": assessment.phase_gate_code,
        "status": assessment.status,
        "total_cleared": assessment.total_cleared,
        "total_uncleared": assessment.total_uncleared,
        "mentor_notes": assessment.mentor_notes,
        "submitted_at": assessment.submitted_at.isoformat() if assessment.submitted_at else None,
        "created_at": assessment.created_at.isoformat(),
        "updated_at": assessment.updated_at.isoformat(),
        "dimensions": [
            {
                "id": str(d.id),
                "dimension_key": d.dimension_key,
                "dimension_title": d.dimension_title,
                "sequence": d.sequence,
                "is_cleared": d.is_cleared,
                "mentor_comment": d.mentor_comment,
            }
            for d in dims
        ],
    }


# ---------------------------------------------------------------------------
# Admin – unlock VALIDATE and send congratulations emails
# ---------------------------------------------------------------------------

_CONGRATS_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Congratulations!</title></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f8;padding:0;margin:0;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%);padding:40px 48px;text-align:center;">
            <h1 style="color:#ffffff;margin:0;font-size:28px;font-weight:700;letter-spacing:-0.5px;">
              🎉 You've Advanced to VALIDATE!
            </h1>
          </td>
        </tr>
        <tr>
          <td style="padding:40px 48px;">
            <p style="color:#374151;font-size:16px;line-height:1.7;margin:0 0 16px;">
              Dear <strong>{first_name}</strong>,
            </p>
            <p style="color:#374151;font-size:16px;line-height:1.7;margin:0 0 16px;">
              Congratulations! Your team <strong>{team_name}</strong> has successfully cleared the
              <strong>DISCOVER → VALIDATE Phase Gate</strong> assessment.
            </p>
            <p style="color:#374151;font-size:16px;line-height:1.7;margin:0 0 24px;">
              Your mentor's evaluation confirmed that your team has demonstrated the insight,
              market understanding, and collaboration needed to progress into the
              <strong>VALIDATE</strong> phase of the DLIF Fellowship.
            </p>
            <table cellpadding="0" cellspacing="0" style="background:#f0f7ff;border-left:4px solid #1a73e8;border-radius:4px;margin:0 0 24px;">
              <tr>
                <td style="padding:16px 20px;">
                  <p style="margin:0;color:#1a73e8;font-weight:700;font-size:14px;text-transform:uppercase;letter-spacing:0.5px;">
                    What Comes Next
                  </p>
                  <p style="margin:8px 0 0;color:#374151;font-size:14px;line-height:1.6;">
                    Log in to your Fellow Portal and navigate to the <strong>VALIDATE</strong> phase
                    to begin your next 4-week journey of prototyping, testing, and proving your solution.
                  </p>
                </td>
              </tr>
            </table>
            <p style="color:#6b7280;font-size:14px;line-height:1.6;margin:0;">
              Keep up the incredible momentum. The DLIF team is proud of your progress!
            </p>
          </td>
        </tr>
        <tr>
          <td style="background:#f9fafb;padding:24px 48px;text-align:center;border-top:1px solid #e5e7eb;">
            <p style="color:#9ca3af;font-size:12px;margin:0;">
              DLIF Platform &bull; DegreeLabs Innovation Fellowship
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_CONGRATS_TEXT = """\
Congratulations, {first_name}!

Your team "{team_name}" has successfully cleared the DISCOVER -> VALIDATE Phase Gate.

Your mentor's assessment confirmed that your team demonstrated the insight, market
understanding, and collaboration needed to advance.

Log in to your Fellow Portal to access the VALIDATE phase and begin your next journey.

-- The DLIF Team
"""


def unlock_validate_and_notify(
    db: Session,
    team_id: UUID,
    admin_user_id: UUID,
    admin_notes: str | None = None,
) -> dict:
    """
    Admin action:
      1. Mark VALIDATE progression as 'approved' for the team.
      2. Send a personalised congratulations email to each Fellow in the team.
    Returns a summary dict.
    """
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found."
        )

    # Unlock VALIDATE
    upsert_team_phase_progression(
        db=db,
        team_id=team_id,
        cohort_id=team.cohort_id,
        phase_code="VALIDATE",
        status="approved",
        decision="shortlisted",
        decided_by_user_id=admin_user_id,
        notes=admin_notes,
    )

    # Fetch all active fellows in the team
    memberships = db.scalars(
        select(TeamMembership).where(TeamMembership.team_id == team_id)
    ).all()

    emails_sent: list[str] = []
    emails_failed: list[str] = []

    for membership in memberships:
        fellow = db.get(User, membership.user_id)
        if not fellow or not fellow.email:
            continue

        html = _CONGRATS_HTML.format(
            first_name=fellow.first_name or "Fellow",
            team_name=team.name,
        )
        text = _CONGRATS_TEXT.format(
            first_name=fellow.first_name or "Fellow",
            team_name=team.name,
        )

        try:
            send_email(
                to_address=fellow.email,
                subject=f"🎉 Congratulations! {team.name} advances to VALIDATE – DLIF Fellowship",
                html_body=html,
                text_body=text,
            )
            emails_sent.append(fellow.email)
        except Exception:
            emails_failed.append(fellow.email)

    return {
        "team_id": str(team_id),
        "team_name": team.name,
        "validate_unlocked": True,
        "emails_sent_count": len(emails_sent),
        "emails_failed_count": len(emails_failed),
    }


def get_gate_assessment_for_fellow(
    db: Session,
    team_id: UUID,
    gate_code: str,
) -> dict | None:
    """
    Read-only view for Fellows – returns None if not yet submitted.
    """
    assessment = db.scalar(
        select(PhaseGateAssessment).where(
            PhaseGateAssessment.team_id == team_id,
            PhaseGateAssessment.phase_gate_code == gate_code,
            PhaseGateAssessment.status == "submitted",
        )
    )
    if not assessment:
        return None
    return serialize_assessment(assessment)


def list_gate_assessments_for_admin(
    db: Session,
    gate_code: str,
    cohort_id: UUID | None = None,
) -> list[dict]:
    """
    Admin: list all submitted assessments (optionally filtered by cohort).
    """
    q = select(PhaseGateAssessment).where(
        PhaseGateAssessment.phase_gate_code == gate_code,
        PhaseGateAssessment.status == "submitted",
    )
    if cohort_id:
        q = q.where(PhaseGateAssessment.cohort_id == cohort_id)

    assessments = db.scalars(q).all()
    return [serialize_assessment(a) for a in assessments]
