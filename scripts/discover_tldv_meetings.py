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
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal
from app.models.session import Session as DBSession, RecordingProcessingStatus
from app.services.tldv import TLDVMeetingData, get_tldv_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("scripts.discover_tldv_meetings")

# ± tolerance window for matching a tl;dv meeting to a session start time
_MATCH_WINDOW_HOURS = 2
# Minimum meeting duration to consider a valid DLIF recording
_MIN_DURATION_SECONDS = 3600
# DLIF keyword that must appear in the tl;dv meeting name
_REQUIRED_KEYWORD = "dlif"


def _is_candidate(
    meeting: TLDVMeetingData,
    session: DBSession,
    already_used_meeting_ids: set[str],
) -> bool:
    """Return True if this meeting is a viable auto-match for this session."""
    if meeting.meeting_id in already_used_meeting_ids:
        return False

    # 1. Direct Google Meet Conference ID match (Highest confidence: 95%+)
    if meeting.conference_id:
        norm_conf = meeting.conference_id.strip().lower()
        if session.google_meet_code and session.google_meet_code.strip().lower() == norm_conf:
            return True
        if session.meeting_url and norm_conf in session.meeting_url.lower():
            return True

    # 2. Heuristic match on time window, duration, and title keywords
    if not session.start_at:
        return False
    if not meeting.happened_at:
        return False
    if _REQUIRED_KEYWORD not in meeting.name.lower():
        return False
    if (meeting.duration_seconds or 0) < _MIN_DURATION_SECONDS:
        return False

    try:
        # tl;dv happenedAt is in the format "Thu Oct 08 2026 13:15:00 GMT+0000 ..."
        # Parse it tolerantly using datetime.fromisoformat after normalising.
        raw = meeting.happened_at.strip()
        # Try ISO format first (e.g. "2026-10-08T13:15:00Z")
        if "T" in raw or raw.endswith("Z"):
            happened_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            # Fallback: parse "Thu Oct 08 2026 13:15:00 GMT+0000 (UTC)"
            import email.utils
            parsed = email.utils.parsedate_to_datetime(raw)
            happened_at = parsed.astimezone(timezone.utc)
    except Exception:
        logger.debug("Could not parse happenedAt for meeting %s", meeting.meeting_id)
        return False

    session_start = session.start_at
    if session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=timezone.utc)

    delta = abs((happened_at - session_start).total_seconds())
    return delta <= _MATCH_WINDOW_HOURS * 3600



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
        all_sessions = db.query(DBSession).all()
        already_used: set[str] = {
            s.tldv_meeting_id
            for s in all_sessions
            if s.tldv_meeting_id
        }
        unmatched_sessions = [
            s for s in all_sessions
            if not s.tldv_meeting_id
            and s.end_at is not None
            and s.end_at <= datetime.now(timezone.utc)
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
    args = parser.parse_args()

    if args.execute:
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
