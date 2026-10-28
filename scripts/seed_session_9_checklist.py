"""Seed constant checklist items for Session 9: Strategy Review.

Idempotent: safe to run multiple times. Existing records are updated in-place;
no duplicates are created.

Session 9 items use session_number=9 and cohort_id=NULL so they automatically
appear for every cohort's Session 9 without any cohort-scoped wiring.

Usage (from project root):
    python scripts/seed_session_9_checklist.py
"""

import sys
from pathlib import Path

# Ensure the project root is on the path so app imports work.
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.checklist import ChecklistItem


# ---------------------------------------------------------------------------
# Canonical Session 9 checklist items
# ---------------------------------------------------------------------------

SESSION_9_ITEMS = [
    {
        "title": "Session - 9: Completed watching the recorded session",
        "description": (
            "Confirm that you have watched the full recorded session for "
            "DISCOVER – Session 9: Strategy Review."
        ),
        "category": "Session Completion",
        "sequence": 1,
    },
    {
        "title": "Session - 9: Completed uploading and demonstrating the PPT",
        "description": (
            "Confirm that you have uploaded your Strategy Review PPT and "
            "demonstrated it as required for Session 9."
        ),
        "category": "Session Completion",
        "sequence": 2,
    },
]

# We identify system items by their exact title so the seeder is idempotent
# even if the UUID changes across environments.
SYSTEM_TITLES = {item["title"] for item in SESSION_9_ITEMS}


def seed(db) -> None:
    existing: dict[str, ChecklistItem] = {}

    # Fetch any already-seeded Session 9 system items by title.
    rows = db.scalars(
        select(ChecklistItem).where(
            ChecklistItem.session_number == 9,
            ChecklistItem.is_system.is_(True),
        )
    ).all()
    for row in rows:
        existing[row.title] = row

    created = 0
    updated = 0

    for spec in SESSION_9_ITEMS:
        title = spec["title"]
        item = existing.get(title)

        if item is None:
            # Create a brand-new system item.
            item = ChecklistItem(
                session_number=9,
                is_system=True,
                is_required=True,
                is_active=True,
                title=title,
                description=spec["description"],
                category=spec["category"],
                sequence=spec["sequence"],
                # cohort_id, team_id, phase_id, week_id, session_id all NULL
                # so the item is genuinely global.
            )
            db.add(item)
            created += 1
            print(f"  [CREATED] {title!r}")
        else:
            # Update mutable fields without touching the UUID or completions.
            changed = False
            for field, value in spec.items():
                if getattr(item, field) != value:
                    setattr(item, field, value)
                    changed = True
            if not item.is_system:
                item.is_system = True
                changed = True
            if not item.is_active:
                item.is_active = True
                changed = True
            if not item.is_required:
                item.is_required = True
                changed = True
            if changed:
                updated += 1
                print(f"  [UPDATED] {title!r}")
            else:
                print(f"  [UNCHANGED] {title!r}")

    db.commit()
    print(f"\nDone. created={created} updated={updated} unchanged={len(SESSION_9_ITEMS) - created - updated}")


def main() -> None:
    print("Seeding Session 9 constant checklist items...")
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
