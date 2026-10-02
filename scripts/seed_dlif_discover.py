"""Seed the DLIF DISCOVER curriculum for one existing Cohort.

Idempotent and controlled by ``DLIF_SEED_COHORT_CODE``. Existing canonical
Week and Session metadata is synchronized without overwriting operational
fields.
"""

import os
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.program import Program
from app.services.discover_initialization import (
    initialize_discover_sessions_for_cohort,
)


def seed_dlif() -> None:
    db = SessionLocal()
    try:
        print("[SEED] Seeding DLIF Program, Phase, Weeks, and Sessions...")

        program = db.scalars(
            select(Program).where(Program.code == "DLIF")
        ).first()
        if not program:
            program = Program(
                name="DegreeLabs Impact Fellowship",
                code="DLIF",
                description=None,
                is_active=True,
            )
            db.add(program)
            db.flush()
            print(f"  + Created Program: {program.name} ({program.code})")
        else:
            program.name = "DegreeLabs Impact Fellowship"
            program.description = None
            print(f"  * Existing Program: {program.name}")

        target_cohort_code = os.getenv(
            "DLIF_SEED_COHORT_CODE",
            "BATCH01",
        )
        cohort = db.scalars(
            select(Cohort).where(
                Cohort.program_id == program.id,
                Cohort.code == target_cohort_code,
            )
        ).first()
        if not cohort:
            raise RuntimeError(
                "Target DLIF Cohort does not exist: "
                f"{target_cohort_code}. Create the Cohort in Admin first or "
                "set DLIF_SEED_COHORT_CODE."
            )
        print(f"  * Target Cohort: {cohort.name} ({cohort.code})")

        phase = db.scalars(
            select(Phase).where(
                Phase.program_id == program.id,
                Phase.code == "DISCOVER",
            )
        ).first()
        if not phase:
            phase = Phase(
                program_id=program.id,
                code="DISCOVER",
                name="DISCOVER",
                development_role="THINK",
                sequence=1,
                duration_weeks=4,
                description=(
                    "A 4-week strategic problem-solving apprenticeship."
                ),
                is_active=True,
            )
            db.add(phase)
            db.flush()
            print(f"  + Created Phase: {phase.name} ({phase.development_role})")
        else:
            phase.name = "DISCOVER"
            phase.development_role = "THINK"
            phase.sequence = 1
            phase.duration_weeks = 4
            phase.description = (
                "A 4-week strategic problem-solving apprenticeship."
            )
            phase.is_active = True
            print(f"  * Existing Phase: {phase.name}")

        initialization = initialize_discover_sessions_for_cohort(db, cohort)
        for week_number in initialization.created_week_numbers:
            print(f"  + Created canonical Week {week_number}")
        for week_number in initialization.synced_week_numbers:
            print(f"  ~ Synced canonical Week {week_number}")
        for week_number in initialization.unchanged_week_numbers:
            print(f"  * Unchanged canonical Week {week_number}")
        for session_number in initialization.created_session_numbers:
            print(f"  + Created canonical Session {session_number}")
        for session_number in initialization.synced_session_numbers:
            print(f"  ~ Synced canonical Session {session_number}")
        for session_number in initialization.unchanged_session_numbers:
            print(f"  * Unchanged canonical Session {session_number}")

        db.commit()
        print("[SUCCESS] DLIF seed complete!")
    except Exception as exc:
        db.rollback()
        print(f"[ERROR] Error seeding DLIF: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_dlif()
