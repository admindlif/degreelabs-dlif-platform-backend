"""CLI tool to view, unlock, or lock fellowship phases for teams.

Usage:
    # List all teams and their progression status:
    python scripts/set_team_progression.py --list

    # Unlock VALIDATE for a team by ID or name:
    python scripts/set_team_progression.py --team "Team Alpha" --phase VALIDATE --action unlock --notes "Shortlisted by Acme Corp"

    # Unlock GROW for a team:
    python scripts/set_team_progression.py --team "Team Alpha" --phase GROW --action unlock --notes "Paid implementation approved"

    # Lock / Revoke a phase:
    python scripts/set_team_progression.py --team "Team Alpha" --phase VALIDATE --action lock
"""

import argparse
import sys
from pathlib import Path
from uuid import UUID

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.progression import TeamPhaseProgression
from app.models.team import Team
from app.repositories.progression import upsert_team_phase_progression


def list_teams(db) -> None:
    teams = db.scalars(select(Team).order_by(Team.name)).all()
    if not teams:
        print("[INFO] No teams found in the database.")
        return

    print(f"\n{'TEAM NAME':<30} {'TEAM ID':<38} {'COMPANY':<20}")
    print("-" * 92)
    for team in teams:
        print(f"{team.name:<30} {str(team.id):<38} {team.company_name or 'N/A':<20}")
        progressions = db.scalars(
            select(TeamPhaseProgression).where(TeamPhaseProgression.team_id == team.id)
        ).all()
        if progressions:
            for p in progressions:
                print(f"   -> Phase {p.phase_code:<10} Status: {p.status:<10} Decision: {p.decision or 'none':<28} (Updated: {p.updated_at})")
        else:
            print("   -> No progression overrides (VALIDATE: locked, GROW: locked)")
    print("\n")


def find_team(db, team_identifier: str) -> Team | None:
    # Try by UUID first
    try:
        team_uuid = UUID(team_identifier)
        team = db.scalar(select(Team).where(Team.id == team_uuid))
        if team:
            return team
    except ValueError:
        pass

    # Try exact name, then case-insensitive prefix/contains
    team = db.scalar(select(Team).where(Team.name == team_identifier))
    if not team:
        team = db.scalar(select(Team).where(Team.name.ilike(f"%{team_identifier}%")))
    return team


def main():
    parser = argparse.ArgumentParser(description="Manage DLIF team phase progression")
    parser.add_argument("--list", action="store_true", help="List all teams and their progression status")
    parser.add_argument("--team", type=str, help="Team name or UUID")
    parser.add_argument("--phase", type=str, choices=["VALIDATE", "GROW"], help="Phase to modify")
    parser.add_argument("--action", type=str, choices=["unlock", "lock"], help="Action to perform")
    parser.add_argument("--notes", type=str, default=None, help="Optional decision notes")

    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            list_teams(db)
            return

        if not args.team or not args.phase or not args.action:
            parser.print_help()
            print("\n[ERROR] When modifying progression, --team, --phase, and --action are all required.")
            return

        team = find_team(db, args.team)
        if not team:
            print(f"[ERROR] Could not find team matching '{args.team}'")
            return

        phase_code = args.phase.upper()
        if args.action == "unlock":
            status = "unlocked"
            decision = "company_shortlisted" if phase_code == "VALIDATE" else "paid_implementation_approved"
        else:
            status = "locked"
            decision = "rejected"

        notes = args.notes or f"Set to {status} via set_team_progression CLI"

        res = upsert_team_phase_progression(
            db=db,
            team_id=team.id,
            cohort_id=team.cohort_id,
            phase_code=phase_code,
            status=status,
            decision=decision,
            decided_by_user_id=None,
            notes=notes,
        )

        print(f"\n[SUCCESS] Updated {team.name} ({team.id}):")
        print(f"  Phase:    {res.phase_code}")
        print(f"  Status:   {res.status}")
        print(f"  Decision: {res.decision}")
        print(f"  Notes:    {res.notes}\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
