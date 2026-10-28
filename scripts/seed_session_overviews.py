"""Seed canonical global session overviews from DLIF Fellow Handbook – DISCOVER v1.0.

Idempotent: running multiple times updates existing records without creating duplicates.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.program_session_overview import ProgramSessionOverview


WEEK_1_PURPOSE = (
    "Build enough business context and evidence to define the problem that deserves attention. "
    "You are deliberately prevented from jumping to solutions."
)

WEEK_2_PURPOSE = (
    "Prevent idea fixation. Your team must create materially different strategic possibilities, "
    "surface the assumptions behind each, test the most important barriers and earn the right to choose."
)

WEEK_3_PURPOSE = (
    "Convert the selected possibility into an integrated strategy and an execution architecture "
    "credible enough to survive contact with the company’s real constraints."
)

WEEK_4_PURPOSE = (
    "Structure the recommendation as an argument for action, not a report of everything the team did."
)


CANONICAL_OVERVIEWS = [
    # WEEK 1 (Page 5)
    {
        "phase_code": "DISCOVER",
        "session_number": 1,
        "purpose": WEEK_1_PURPOSE,
        "what_to_explore": "Business Context & Evidence",
        "working_evidence": (
            "Lean company context; relevant workflow; stakeholders; "
            "facts/assumptions/unknowns; clarification questions."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 5,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 2,
        "purpose": WEEK_1_PURPOSE,
        "what_to_explore": "Problem Framing & Diagnosis",
        "working_evidence": (
            "Evidence-backed problem frame; value leakage; research synthesis; "
            "outcome definition; unresolved unknowns."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 5,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 3,
        "purpose": WEEK_1_PURPOSE,
        "what_to_explore": "Discovery Review",
        "working_evidence": (
            "Revised Business Diagnosis & Problem Framing Pack after mentor challenge."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 5,
    },
    # WEEK 2 (Page 6)
    {
        "phase_code": "DISCOVER",
        "session_number": 4,
        "purpose": WEEK_2_PURPOSE,
        "what_to_explore": "Research & Possibility Generation",
        "working_evidence": (
            "Research synthesis; three distinct possibilities; trade-offs and constraints."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 6,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 5,
        "purpose": WEEK_2_PURPOSE,
        "what_to_explore": "What Would Have to Be True?",
        "working_evidence": (
            "Assumption map; WWHTBT analysis; barrier tests; evidence gathered or still required."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 6,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 6,
        "purpose": WEEK_2_PURPOSE,
        "what_to_explore": "Strategic Choice Review",
        "working_evidence": (
            "Revised Strategic Possibility & Choice Pack; chosen direction plus rejected alternatives."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 6,
    },
    # WEEK 3 (Page 7)
    {
        "phase_code": "DISCOVER",
        "session_number": 7,
        "purpose": WEEK_3_PURPOSE,
        "what_to_explore": "Integrated Strategy Choices",
        "working_evidence": (
            "Aspiration; where to play; how to win; required capabilities; "
            "management systems; explicit trade-offs and boundaries."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 7,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 8,
        "purpose": WEEK_3_PURPOSE,
        "what_to_explore": "Execution Architecture",
        "working_evidence": (
            "Implementation phases; workflow/solution architecture; roles; "
            "technology/data; dependencies; risks; success measures."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 7,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 9,
        "purpose": WEEK_3_PURPOSE,
        "what_to_explore": "Strategy Review",
        "working_evidence": (
            "Revised Strategy & Execution Blueprint after coherence and feasibility challenge."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 7,
    },
    # WEEK 4 (Page 8)
    {
        "phase_code": "DISCOVER",
        "session_number": 10,
        "purpose": WEEK_4_PURPOSE,
        "what_to_explore": "Proposal Architecture",
        "working_evidence": (
            "Situation; problem; evidence; alternatives; recommendation; "
            "execution; expected impact; risks; validation needs."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 8,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 11,
        "purpose": WEEK_4_PURPOSE,
        "what_to_explore": "Executive Communication",
        "working_evidence": (
            "Executive summary; presentation; Q&A preparation; unsupported claims removed."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 8,
    },
    {
        "phase_code": "DISCOVER",
        "session_number": 12,
        "purpose": WEEK_4_PURPOSE,
        "what_to_explore": "Final DISCOVER Review",
        "working_evidence": (
            "Final Executive Proposal, Company Presentation and Strategic Design Portfolio."
        ),
        "source_document": "DLIF Fellow Handbook – DISCOVER",
        "source_version": "1.0",
        "source_page": 8,
    },
]


def seed_session_overviews() -> None:
    db = SessionLocal()
    try:
        print("[SEED] Seeding Global Program Session Overviews (DISCOVER Sessions 1–12)...")
        created_count = 0
        updated_count = 0

        for item in CANONICAL_OVERVIEWS:
            existing = db.scalars(
                select(ProgramSessionOverview).where(
                    ProgramSessionOverview.phase_code == item["phase_code"],
                    ProgramSessionOverview.session_number == item["session_number"],
                    ProgramSessionOverview.source_version == item["source_version"],
                )
            ).first()

            if existing:
                existing.purpose = item["purpose"]
                existing.what_to_explore = item["what_to_explore"]
                existing.working_evidence = item["working_evidence"]
                existing.source_document = item["source_document"]
                existing.source_page = item["source_page"]
                existing.is_active = True
                updated_count += 1
            else:
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
                created_count += 1

        db.commit()
        print(f"[SEED] Done. Created: {created_count}, Updated: {updated_count}. Total: {len(CANONICAL_OVERVIEWS)}")
    except Exception as exc:
        db.rollback()
        print(f"[SEED ERROR] Failed to seed session overviews: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_session_overviews()
