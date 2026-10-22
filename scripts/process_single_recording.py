"""
Process one selected DLIF session recording.
Default behavior is read-only. Use --execute to start processing.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal
from app.models.session import (
    RecordingProcessingStatus,
    Session as DBSession,
    SessionStatus,
)
from app.services.recording_processor import (
    process_session_recording_to_r2,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        session = db.get(DBSession, args.session_id)

        if session is None:
            raise SystemExit("Session not found.")

        print("Session:", session.session_number, session.title)
        print("Status:", session.recording_processing_status)
        print("tl;dv linked:", bool(session.tldv_meeting_id))

        if session.status == SessionStatus.CANCELLED:
            raise SystemExit("Cancelled session. Processing blocked.")

        if session.end_at is None or session.end_at > datetime.now(timezone.utc):
            raise SystemExit("Session has not ended. Processing blocked.")

        if not session.tldv_meeting_id:
            raise SystemExit("No tl;dv meeting linked. Processing blocked.")

        if session.recording_processing_status != RecordingProcessingStatus.PENDING:
            raise SystemExit("Session is not PENDING. Processing blocked.")

    if not args.execute:
        print("DRY RUN: Session is eligible. No processing started.")
        return

    result = process_session_recording_to_r2(args.session_id)
    print("Processing result:", result.status)


if __name__ == "__main__":
    main()
