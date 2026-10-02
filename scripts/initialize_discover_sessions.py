"""Create or synchronize canonical DISCOVER Weeks and Cohort Sessions."""

import argparse
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.cohort import Cohort
from app.services.discover_initialization import (
    initialize_discover_sessions_for_cohort,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ensure canonical DISCOVER Weeks 1-4 and Sessions 0-12 exist, "
            "synchronizing curriculum metadata without changing operational "
            "fields."
        )
    )
    parser.add_argument(
        "--cohort-code",
        required=True,
        help="Exact code of the existing Cohort to initialize.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = SessionLocal()
    try:
        cohort = db.scalars(
            select(Cohort).where(Cohort.code == args.cohort_code)
        ).first()
        if not cohort:
            raise RuntimeError(
                f"Cohort with code '{args.cohort_code}' was not found."
            )

        result = initialize_discover_sessions_for_cohort(db, cohort)
        db.commit()

        print(f"Cohort: {cohort.name} ({cohort.code})")
        print("\nWEEKS")
        week_actions = {
            **{number: "CREATED" for number in result.created_week_numbers},
            **{number: "SYNCED" for number in result.synced_week_numbers},
            **{
                number: "UNCHANGED"
                for number in result.unchanged_week_numbers
            },
        }
        for week_number in sorted(week_actions):
            print(f"{week_actions[week_number]:<9} Week {week_number}")

        print("\nSESSIONS")
        session_actions = {
            **{
                number: "CREATED"
                for number in result.created_session_numbers
            },
            **{
                number: "SYNCED"
                for number in result.synced_session_numbers
            },
            **{
                number: "UNCHANGED"
                for number in result.unchanged_session_numbers
            },
        }
        for session_number in sorted(session_actions):
            print(
                f"{session_actions[session_number]:<9} "
                f"Session {session_number}"
            )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
