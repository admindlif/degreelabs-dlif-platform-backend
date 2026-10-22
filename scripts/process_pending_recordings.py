"""
CLI entry point to process pending session recordings to Cloudflare R2.
Intended to be invoked by Render Cron jobs or manual operator runs.

Usage:
    python scripts/process_pending_recordings.py [--limit N]
"""

import argparse
import logging
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.recording_jobs import process_pending_recordings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("scripts.process_pending_recordings")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process pending tl;dv recordings to Cloudflare R2",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of sessions to process in this run (default: 10)",
    )
    args = parser.parse_args()

    logger.info("Starting batch recording processor (limit=%d)...", args.limit)

    try:
        summary = process_pending_recordings(limit=args.limit)
    except Exception as exc:
        logger.exception("Fatal error during recording batch execution: %s", exc)
        return 1

    print("========================================")
    print("Recording Processing Batch Summary")
    print("========================================")
    print(f"Scanned candidates : {summary.scanned}")
    print(f"Processed          : {summary.processed}")
    print(f"Ready in R2        : {summary.ready}")
    print(f"Pending/Not Ready  : {summary.pending}")
    print(f"Failed             : {summary.failed}")
    print(f"Skipped            : {summary.skipped}")
    print("========================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
