"""
tl;dv Meeting Discovery Script

Discovers unmatched tl;dv meetings and proposes safe automatic matches
against DLIF sessions that have no tldv_meeting_id yet.

Usage:
    python scripts/discover_tldv_meetings.py           # Dry-run: report only
    python scripts/discover_tldv_meetings.py --execute # Write matches to DB

Matching rules (all must pass for auto-assign):
    1. Exactly ONE tl;dv meeting falls within ±2 hours of session.start_at.
    2. Meeting name contains "dlif" (case-insensitive).
    3. Meeting duration >= 3600 seconds (at least 1 hour long).
    4. No other session already references that meeting_id.

Ambiguous cases (two or more candidates, or zero for a session) are reported
but never auto-assigned. An admin must set tldv_meeting_id manually.

Security:
    - Never logs API keys, signed URLs, or raw meeting payloads.
    - --execute requires explicit confirmation before writing.
"""

import argparse
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
import sys

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy.orm import joinedload
from app.db.session import SessionLocal
from app.models.cohort import Cohort
from app.models.session import (
    RecordingProcessingStatus,
    Session as DBSession,
    SessionStatus,
)
from app.services.tldv import TLDVMeetingData, get_tldv_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("scripts.discover_tldv_meetings")

# ± tolerance window for matching a tl;dv meeting to a session start time
_MATCH_WINDOW_HOURS = 2
# Minimum meeting duration to consider a valid DLIF recording (30 mins)
_MIN_DURATION_SECONDS = 1800
# DLIF keyword that must appear in the tl;dv meeting name
_REQUIRED_KEYWORD = "dlif"


def parse_happened_at(raw: str | None) -> datetime | None:
    """Tolerantly parse tl;dv happenedAt timestamp to a UTC-aware datetime."""
    if not raw:
        return None
    raw = raw.strip()
    # Try ISO format
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1] + "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        pass
    # Try email/RFC 2822 format (e.g. 'Fri Oct 16 2026 13:32:08 GMT+0000 (Coordinated Universal Time)')
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    # Fallback to dateutil parser
    try:
        from dateutil import parser
        parsed = parser.parse(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        logger.debug("Could not parse happenedAt: %s", raw)
        return None


def _extract_batch_number(text: str | None) -> int | None:
    """
    Extract the numeric batch number from a cohort name or meeting title.

    Examples:
        "Batch 02"                                            -> 2
        "DLIF Batch 2"                                        -> 2
        "DLIF Induction (Batch 02)"                           -> 2
        "DLIF Discover Batch 2 Session-02|Problem Framing..." -> 2
        "DLIF Discover Batch-2 Session 01"                    -> 2
        "Cohort 1"                                            -> None
    """
    if not text:
        return None
    m = re.search(r"\bbatch[ -]*0*(\d+)\b", text, re.I)
    return int(m.group(1)) if m else None


def _is_candidate(
    meeting: TLDVMeetingData,
    session: DBSession,
    already_used_meeting_ids: set[str],
) -> bool:
    """Return True if this meeting is a viable auto-match for this session."""
    if meeting.meeting_id in already_used_meeting_ids:
        return False

    name_lower = (meeting.name or "").lower()

    # 1. Direct Google Meet Conference ID match (Highest confidence: 95%+)
    if meeting.conference_id:
        norm_conf = meeting.conference_id.strip().lower()
        if session.google_meet_code and session.google_meet_code.strip().lower() == norm_conf:
            return True
        if session.meeting_url and norm_conf in session.meeting_url.lower():
            return True

    # 2. Batch Number Consistency Check:
    # If the session's cohort specifies a batch number (e.g. Batch 02)
    # AND the meeting title also specifies a batch number (e.g. Batch 01),
    # disqualify immediately to prevent cross-batch contamination.
    cohort_name = getattr(session.cohort, "name", None) if hasattr(session, "cohort") and session.cohort else None
    cohort_batch_num = _extract_batch_number(cohort_name)
    meeting_batch_num = _extract_batch_number(meeting.name)

    if cohort_batch_num is not None and meeting_batch_num is not None:
        if cohort_batch_num != meeting_batch_num:
            return False

    # 3. Strong match on "dlif" + session number / title
    if _REQUIRED_KEYWORD in name_lower:
        s_num = session.session_number
        patterns = [
            rf"\bsession[ -]*0?{s_num}\b",
            rf"\bsession[ -]*{s_num:02d}\b",
        ]
        if s_num == 0:
            patterns.extend([
                r"\binduction\b",
                r"\bsession[ -]*0?0\b",
            ])

        for pat in patterns:
            if re.search(pat, name_lower):
                # Also verify title match or minimum meeting duration
                if session.title and session.title.lower() in name_lower:
                    return True
                if (meeting.duration_seconds or 0) >= _MIN_DURATION_SECONDS:
                    return True

    # 4. Time window heuristic match (within ±2h of session.start_at)
    happened_at = parse_happened_at(meeting.happened_at)
    if (
        session.start_at
        and happened_at
        and _REQUIRED_KEYWORD in name_lower
        and (meeting.duration_seconds or 0) >= _MIN_DURATION_SECONDS
    ):
        session_start = session.start_at
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=timezone.utc)

        delta = abs((happened_at - session_start).total_seconds())
        if delta <= _MATCH_WINDOW_HOURS * 3600:
            return True

    return False


def _disambiguate_candidates(
    session: DBSession,
    candidates: list[TLDVMeetingData],
) -> list[TLDVMeetingData]:
    """
    Safely resolve multiple candidate meetings for a session.
    Prioritizes:
    1. Candidate matching the specific session title (e.g. 'Problem Framing & Diagnosis')
    2. Candidate explicitly matching the cohort batch number
    3. Full session recording (>= 3600s) over shorter snippets (e.g. 1h 40m vs 31m)
    4. Candidate with happened_at closest to session.start_at
    """
    if len(candidates) <= 1:
        return candidates

    # 1. Prefer candidate whose meeting title contains the session's specific title
    if session.title and len(session.title.strip()) > 3:
        norm_title = session.title.strip().lower()
        title_matches = [
            m for m in candidates
            if norm_title in (m.name or "").lower()
        ]
        if len(title_matches) == 1:
            return title_matches
        if len(title_matches) > 1:
            candidates = title_matches

    # 2. Prefer candidate explicitly matching the cohort's batch number
    cohort_name = getattr(session.cohort, "name", None) if hasattr(session, "cohort") and session.cohort else None
    cohort_batch_num = _extract_batch_number(cohort_name)
    if cohort_batch_num is not None:
        batch_matches = [
            m for m in candidates
            if _extract_batch_number(m.name) == cohort_batch_num
        ]
        if len(batch_matches) == 1:
            return batch_matches
        if len(batch_matches) > 1:
            candidates = batch_matches

    # 3. Prefer full session recordings over short snippets (e.g. 1h 40m vs 31m)
    long_candidates = [
        m for m in candidates
        if (m.duration_seconds or 0) >= 3600
    ]
    if len(long_candidates) == 1:
        return long_candidates
    elif len(long_candidates) > 1:
        candidates = long_candidates

    # 4. If session.start_at is known, pick the one closest to session.start_at
    if session.start_at and len(candidates) > 1:
        session_start = session.start_at
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=timezone.utc)

        def _time_diff(m: TLDVMeetingData) -> float:
            happened = parse_happened_at(m.happened_at)
            if not happened:
                return float("inf")
            return abs((happened - session_start).total_seconds())

        sorted_by_time = sorted(candidates, key=_time_diff)
        diff_0 = _time_diff(sorted_by_time[0])
        diff_1 = _time_diff(sorted_by_time[1])
        if diff_0 <= _MATCH_WINDOW_HOURS * 3600 and (diff_1 - diff_0) >= 1800:
            return [sorted_by_time[0]]

    return candidates



def run_discovery(execute: bool = False) -> int:
    """
    Main discovery logic. Returns exit code (0 = success, 1 = error).
    """
    client = get_tldv_client()
    if client is None:
        print("[FAIL] tl;dv client is not available. Check TLDV_API_KEY and TLDV_MOCK_MODE.")
        return 1

    print("Fetching all tl;dv meetings from account...")
    all_meetings = client.list_meetings()
    print(f"  Found {len(all_meetings)} meeting(s) in tl;dv account.")

    # Build a set of already-used meeting IDs (referenced by any session)
    with SessionLocal() as db:
        all_sessions = (
            db.query(DBSession)
            .options(joinedload(DBSession.cohort))
            .order_by(DBSession.session_number)
            .all()
        )
        already_used: set[str] = {
            s.tldv_meeting_id
            for s in all_sessions
            if s.tldv_meeting_id
        }
        unmatched_sessions = [
            s for s in all_sessions
            if not s.tldv_meeting_id
            and s.status != SessionStatus.CANCELLED
        ]

    print(f"  Sessions already matched to tl;dv: {len(already_used)}")
    print(f"  Sessions needing a tl;dv match:     {len(unmatched_sessions)}")
    print()

    if not unmatched_sessions:
        print("No unmatched sessions found. Nothing to do.")
        return 0

    auto_matches: list[tuple[DBSession, TLDVMeetingData]] = []
    ambiguous: list[tuple[DBSession, list[TLDVMeetingData]]] = []
    no_match: list[DBSession] = []

    for session in unmatched_sessions:
        candidates = [
            m for m in all_meetings
            if _is_candidate(m, session, already_used)
        ]

        if len(candidates) > 1:
            candidates = _disambiguate_candidates(session, candidates)

        if len(candidates) == 1:
            auto_matches.append((session, candidates[0]))
        elif len(candidates) > 1:
            ambiguous.append((session, candidates))
        else:
            no_match.append(session)

    # ── Report ──────────────────────────────────────────────────────────────

    print("=" * 70)
    print(f"AUTO-MATCHES ({len(auto_matches)}) — safe to assign automatically")
    print("=" * 70)
    for session, meeting in auto_matches:
        print(
            f"  Session {session.session_number}: '{session.title}'\n"
            f"    -> tl;dv meeting: '{meeting.name}'\n"
            f"    -> meeting_id: {meeting.meeting_id}\n"
            f"    -> happened_at: {meeting.happened_at}\n"
            f"    -> duration: {meeting.duration_seconds}s\n"
        )

    print("=" * 70)
    print(f"AMBIGUOUS ({len(ambiguous)}) — require admin manual assignment")
    print("=" * 70)
    for session, candidates in ambiguous:
        print(f"  Session {session.session_number}: '{session.title}'")
        for m in candidates:
            print(f"    candidate: '{m.name}' (id={m.meeting_id}, at={m.happened_at})")
        print()

    print("=" * 70)
    print(f"NO MATCH ({len(no_match)}) — no tl;dv meeting found within ±{_MATCH_WINDOW_HOURS}h")
    print("=" * 70)
    for session in no_match:
        print(
            f"  Session {session.session_number}: '{session.title}' "
            f"(start_at={session.start_at})"
        )

    print()

    # ── Execute ──────────────────────────────────────────────────────────────

    if not auto_matches:
        print("No safe auto-matches to apply.")
        return 0

    if not execute:
        print(
            f"DRY RUN: {len(auto_matches)} auto-match(es) found. "
            "Run with --execute to write them to the database."
        )
        return 0

    print(f"Writing {len(auto_matches)} match(es) to the database...")
    written = 0

    with SessionLocal() as db:
        for session, meeting in auto_matches:
            db_session = db.get(DBSession, session.id)
            if db_session is None:
                logger.warning("Session %s not found in DB, skipping.", session.id)
                continue

            if db_session.tldv_meeting_id:
                print(
                    f"  SKIP session {db_session.session_number}: "
                    "tldv_meeting_id was set since discovery started."
                )
                continue

            db_session.tldv_meeting_id = meeting.meeting_id
            db_session.is_unlocked = True

            # Populate missing session timestamps from the tl;dv meeting
            happened_at = parse_happened_at(meeting.happened_at)
            if db_session.start_at is None and happened_at:
                db_session.start_at = happened_at
            if db_session.end_at is None and happened_at:
                duration_sec = meeting.duration_seconds or 3600
                db_session.end_at = happened_at + timedelta(seconds=duration_sec)

            # Ensure session is marked completed since a recorded meeting exists
            if db_session.status != SessionStatus.COMPLETED and db_session.status != SessionStatus.CANCELLED:
                db_session.status = SessionStatus.COMPLETED

            # Reset to PENDING so the batch processor picks it up
            if db_session.recording_processing_status not in (
                RecordingProcessingStatus.READY,
                RecordingProcessingStatus.PROCESSING,
            ):
                db_session.recording_processing_status = RecordingProcessingStatus.PENDING

            db.commit()
            written += 1
            print(
                f"  OK session {db_session.session_number}: "
                f"tldv_meeting_id set to {meeting.meeting_id}"
            )

    print(f"\nDone. {written}/{len(auto_matches)} session(s) updated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and match tl;dv meetings to DLIF sessions"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write auto-matches to the database (default: dry-run only)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation (for CI/GitHub Actions)",
    )
    args = parser.parse_args()

    if args.execute and not args.yes:
        print("=" * 70)
        print("WARNING: --execute will write tldv_meeting_id to the database.")
        print("Only unambiguous matches (exactly one candidate) will be applied.")
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return 0

    return run_discovery(execute=args.execute)


if __name__ == "__main__":
    sys.exit(main())
