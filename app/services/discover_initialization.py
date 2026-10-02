from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.week import Week


@dataclass(frozen=True)
class CanonicalDiscoverWeek:
    week_number: int
    sequence: int
    title: str
    strategic_question: str
    description: str | None


CANONICAL_DISCOVER_WEEKS = (
    CanonicalDiscoverWeek(
        1,
        1,
        "DISCOVER THE REAL PROBLEM",
        "What is really happening here?",
        "Build enough business context and evidence to define the problem "
        "that deserves attention.",
    ),
    CanonicalDiscoverWeek(
        2,
        2,
        "CREATE STRATEGIC POSSIBILITIES",
        "What could we choose to do?",
        "Prevent idea fixation. Your team must create materially different "
        "strategic possibilities, surface the assumptions behind each, test "
        "the most important barriers and earn the right to choose.",
    ),
    CanonicalDiscoverWeek(
        3,
        3,
        "DESIGN THE STRATEGY",
        "If this is our choice, how will it actually work?",
        "Convert the selected possibility into an integrated strategy and an "
        "execution architecture credible enough to survive contact with the "
        "company’s real constraints.",
    ),
    CanonicalDiscoverWeek(
        4,
        4,
        "BUILD THE CASE FOR ACTION",
        "Why should the company believe us?",
        None,
    ),
)
CANONICAL_DISCOVER_WEEK_NUMBERS = {
    spec.week_number for spec in CANONICAL_DISCOVER_WEEKS
}


@dataclass(frozen=True)
class CanonicalDiscoverSession:
    session_number: int
    session_type: SessionType
    title: str
    description: str
    week_number: int | None

    @property
    def sequence(self) -> int:
        return self.session_number


CANONICAL_DISCOVER_SESSIONS = (
    CanonicalDiscoverSession(
        0,
        SessionType.INDUCTION,
        "Induction",
        "Understand how the fellowship works, what your team must produce, "
        "how evidence is judged, how mentors support you, and what happens "
        "after the final company review.",
        None,
    ),
    CanonicalDiscoverSession(
        1,
        SessionType.LEARN_WORK,
        "Business Context & Evidence",
        "Lean company context; relevant workflow; stakeholders; "
        "facts/assumptions/unknowns; clarification questions.",
        1,
    ),
    CanonicalDiscoverSession(
        2,
        SessionType.LEARN_WORK,
        "Problem Framing & Diagnosis",
        "Evidence-backed problem frame; value leakage; research synthesis; "
        "outcome definition; unresolved unknowns.",
        1,
    ),
    CanonicalDiscoverSession(
        3,
        SessionType.OUTPUT_REVIEW,
        "Business Diagnosis & Problem Framing Pack Review",
        "Revised Business Diagnosis & Problem Framing Pack after mentor "
        "challenge.",
        1,
    ),
    CanonicalDiscoverSession(
        4,
        SessionType.LEARN_WORK,
        "Research & Possibility Generation",
        "Research synthesis; three distinct possibilities; trade-offs and "
        "constraints.",
        2,
    ),
    CanonicalDiscoverSession(
        5,
        SessionType.LEARN_WORK,
        "What Would Have to Be True?",
        "Assumption map; WWHTBT analysis; barrier tests; evidence gathered "
        "or still required.",
        2,
    ),
    CanonicalDiscoverSession(
        6,
        SessionType.OUTPUT_REVIEW,
        "Strategic Choice Review",
        "Revised Strategic Possibility & Choice Pack; chosen direction plus "
        "rejected alternatives.",
        2,
    ),
    CanonicalDiscoverSession(
        7,
        SessionType.LEARN_WORK,
        "Integrated Strategy Choices",
        "Aspiration; where to play; how to win; required capabilities; "
        "management systems; explicit trade-offs and boundaries.",
        3,
    ),
    CanonicalDiscoverSession(
        8,
        SessionType.LEARN_WORK,
        "Execution Architecture",
        "Implementation phases; workflow/solution architecture; roles; "
        "technology/data; dependencies; risks; success measures.",
        3,
    ),
    CanonicalDiscoverSession(
        9,
        SessionType.OUTPUT_REVIEW,
        "Strategy Review",
        "Revised Strategy & Execution Blueprint after coherence and "
        "feasibility challenge.",
        3,
    ),
    CanonicalDiscoverSession(
        10,
        SessionType.LEARN_WORK,
        "Proposal Architecture",
        "Situation; problem; evidence; alternatives; recommendation; "
        "execution; expected impact; risks; validation needs.",
        4,
    ),
    CanonicalDiscoverSession(
        11,
        SessionType.LEARN_WORK,
        "Executive Communication",
        "Executive summary; presentation; Q&A preparation; unsupported "
        "claims removed.",
        4,
    ),
    CanonicalDiscoverSession(
        12,
        SessionType.OUTPUT_REVIEW,
        "Final DISCOVER Review",
        "Final Executive Proposal, Company Presentation and Strategic Design "
        "Portfolio.",
        4,
    ),
)
CANONICAL_DISCOVER_SESSION_NUMBERS = {
    spec.session_number for spec in CANONICAL_DISCOVER_SESSIONS
}


class DiscoverSessionInitializationError(ValueError):
    """The selected Program or Cohort cannot be safely synchronized."""


@dataclass(frozen=True)
class DiscoverSessionInitializationResult:
    created_week_numbers: tuple[int, ...]
    synced_week_numbers: tuple[int, ...]
    unchanged_week_numbers: tuple[int, ...]
    created_session_numbers: tuple[int, ...]
    synced_session_numbers: tuple[int, ...]
    unchanged_session_numbers: tuple[int, ...]


def initialize_discover_sessions_for_cohort(
    db: Session,
    cohort: Cohort,
) -> DiscoverSessionInitializationResult:
    """Create or synchronize canonical DISCOVER Weeks and Cohort Sessions.

    Shared curriculum fields are synchronized on Weeks 1-4 and existing
    S0-S12 rows. Week timestamps and unlock state remain intact. Session
    scheduling, access, meeting, recording, submission, and Calendar metadata
    remain Cohort-specific. The caller owns the transaction.
    """

    phase = db.scalars(
        select(Phase).where(
            Phase.program_id == cohort.program_id,
            Phase.code == "DISCOVER",
            Phase.is_active.is_(True),
        )
    ).first()
    if not phase:
        raise DiscoverSessionInitializationError(
            "The selected Program does not have an active DISCOVER Phase."
        )

    existing_weeks = list(
        db.scalars(
            select(Week)
            .where(Week.phase_id == phase.id)
            .order_by(Week.week_number)
        ).all()
    )
    weeks_by_number = {
        week.week_number: week for week in existing_weeks
    }
    weeks_by_sequence = {
        week.sequence: week for week in existing_weeks
    }
    preserved_week_updated_at = {
        week.id: week.updated_at for week in existing_weeks
    }

    for spec in CANONICAL_DISCOVER_WEEKS:
        sequence_owner = weeks_by_sequence.get(spec.sequence)
        if (
            sequence_owner
            and sequence_owner.week_number
            not in CANONICAL_DISCOVER_WEEK_NUMBERS
        ):
            raise DiscoverSessionInitializationError(
                f"Cannot synchronize canonical Week {spec.week_number}: "
                f"sequence {spec.sequence} is used by additional Week "
                f"{sequence_owner.week_number}."
            )

    canonical_weeks_with_drifted_sequence = [
        week
        for week in existing_weeks
        if week.week_number in CANONICAL_DISCOVER_WEEK_NUMBERS
        and week.sequence != week.week_number
    ]
    temporary_week_sequence = (
        min((week.sequence for week in existing_weeks), default=0)
        - len(canonical_weeks_with_drifted_sequence)
        - 1
    )
    for offset, week in enumerate(canonical_weeks_with_drifted_sequence):
        week.sequence = temporary_week_sequence + offset
    if canonical_weeks_with_drifted_sequence:
        db.flush()

    created_week_numbers: list[int] = []
    synced_week_numbers: list[int] = []
    unchanged_week_numbers: list[int] = []

    for spec in CANONICAL_DISCOVER_WEEKS:
        existing_week = weeks_by_number.get(spec.week_number)
        if not existing_week:
            existing_week = Week(
                phase_id=phase.id,
                week_number=spec.week_number,
                sequence=spec.sequence,
                title=spec.title,
                strategic_question=spec.strategic_question,
                description=spec.description,
                unlock_at=None,
            )
            db.add(existing_week)
            weeks_by_number[spec.week_number] = existing_week
            created_week_numbers.append(spec.week_number)
            continue

        preserved_updated_at = preserved_week_updated_at[existing_week.id]
        canonical_values = {
            "week_number": spec.week_number,
            "sequence": spec.sequence,
            "title": spec.title,
            "strategic_question": spec.strategic_question,
            "description": spec.description,
        }
        changed = False
        for field, value in canonical_values.items():
            if getattr(existing_week, field) != value:
                setattr(existing_week, field, value)
                changed = True

        if changed:
            if preserved_updated_at is not None:
                existing_week.updated_at = preserved_updated_at
                flag_modified(existing_week, "updated_at")
            synced_week_numbers.append(spec.week_number)
        else:
            unchanged_week_numbers.append(spec.week_number)

    db.flush()

    missing_week_numbers = sorted(
        CANONICAL_DISCOVER_WEEK_NUMBERS - set(weeks_by_number)
    )
    if missing_week_numbers:
        missing_label = ", ".join(
            str(number) for number in missing_week_numbers
        )
        raise DiscoverSessionInitializationError(
            "Unable to initialize the selected Program's DISCOVER Weeks. "
            f"Missing: {missing_label}."
        )

    existing_sessions = list(
        db.scalars(
            select(DBSession)
            .where(DBSession.cohort_id == cohort.id)
            .order_by(DBSession.sequence)
        ).all()
    )
    sessions_by_number = {
        session.session_number: session
        for session in existing_sessions
    }
    sessions_by_sequence = {
        session.sequence: session
        for session in existing_sessions
    }

    for spec in CANONICAL_DISCOVER_SESSIONS:
        sequence_owner = sessions_by_sequence.get(spec.sequence)
        if (
            sequence_owner
            and sequence_owner.session_number
            not in CANONICAL_DISCOVER_SESSION_NUMBERS
        ):
            raise DiscoverSessionInitializationError(
                f"Cannot synchronize canonical Session {spec.session_number}: "
                f"sequence {spec.sequence} is used by additional Session "
                f"{sequence_owner.session_number}."
            )

    canonical_rows_with_drifted_sequence = [
        session
        for session in existing_sessions
        if session.session_number in CANONICAL_DISCOVER_SESSION_NUMBERS
        and session.sequence != session.session_number
    ]
    temporary_sequence = (
        min((session.sequence for session in existing_sessions), default=0)
        - len(canonical_rows_with_drifted_sequence)
        - 1
    )
    for offset, session in enumerate(canonical_rows_with_drifted_sequence):
        session.sequence = temporary_sequence + offset
    if canonical_rows_with_drifted_sequence:
        db.flush()

    created_numbers: list[int] = []
    synced_numbers: list[int] = []
    unchanged_numbers: list[int] = []
    initial_unlock_at = datetime.now(timezone.utc)

    for spec in CANONICAL_DISCOVER_SESSIONS:
        target_week = (
            weeks_by_number[spec.week_number]
            if spec.week_number is not None
            else None
        )
        target_week_id = target_week.id if target_week else None
        existing_session = sessions_by_number.get(spec.session_number)

        if not existing_session:
            is_initially_unlocked = spec.session_number <= 2
            session = DBSession(
                cohort_id=cohort.id,
                phase_id=phase.id,
                week_id=target_week_id,
                session_number=spec.session_number,
                session_type=spec.session_type,
                title=spec.title,
                description=spec.description,
                start_at=None,
                end_at=None,
                unlock_at=(
                    initial_unlock_at if is_initially_unlocked else None
                ),
                is_unlocked=is_initially_unlocked,
                submission_enabled=False,
                meeting_url=None,
                recording_url=None,
                transcript_url=None,
                status=SessionStatus.SCHEDULED,
                sequence=spec.sequence,
            )
            db.add(session)
            created_numbers.append(spec.session_number)
            continue

        canonical_values = {
            "phase_id": phase.id,
            "week_id": target_week_id,
            "session_type": spec.session_type,
            "title": spec.title,
            "description": spec.description,
            "sequence": spec.sequence,
        }
        changed = False
        for field, value in canonical_values.items():
            if getattr(existing_session, field) != value:
                setattr(existing_session, field, value)
                changed = True

        if changed:
            synced_numbers.append(spec.session_number)
        else:
            unchanged_numbers.append(spec.session_number)

    db.flush()

    return DiscoverSessionInitializationResult(
        created_week_numbers=tuple(created_week_numbers),
        synced_week_numbers=tuple(synced_week_numbers),
        unchanged_week_numbers=tuple(unchanged_week_numbers),
        created_session_numbers=tuple(created_numbers),
        synced_session_numbers=tuple(synced_numbers),
        unchanged_session_numbers=tuple(unchanged_numbers),
    )
