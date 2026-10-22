"""
Recording Jobs Service

Service-layer orchestration for batch processing of pending session recordings to Cloudflare R2.
Scans eligible sessions that have concluded and delegates actual media processing to
the idempotent processor process_session_recording_to_r2.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import re
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.session import (
    RecordingProcessingStatus,
    Session as DBSession,
    SessionStatus,
)
from app.services.recording_processor import process_session_recording_to_r2

logger = logging.getLogger(__name__)


@dataclass
class RecordingBatchResult:
    scanned: int = 0
    processed: int = 0
    ready: int = 0
    pending: int = 0
    failed: int = 0
    skipped: int = 0
    session_ids: list[UUID] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _sanitize_job_error(err: str) -> str:
    """
    Sanitize error message to ensure no credentials, tokens, or signed URLs are logged or stored.
    """
    if not err:
        return "Unknown error"
    sanitized = re.sub(r"https?://[^\s\"']+", lambda m: m.group(0).split("?")[0], err)
    if len(sanitized) > 300:
        sanitized = sanitized[:300]
    return sanitized.strip()


def get_pending_recording_candidates(
    db: Session,
    limit: int = 10,
    as_of: datetime | None = None,
) -> list[DBSession]:
    """
    Select concluded sessions eligible for recording processing.

    Candidate selection rules:
    - end_at IS NOT NULL
    - end_at <= current UTC time (or as_of)
    - status != cancelled (and not None if enum constraint permits)
    - recording_processing_status == pending
    - tldv_meeting_id IS NOT NULL and != ""

    Deterministic ordering:
    - Oldest ended sessions first (end_at ASC, id ASC)
    - Capped at limit
    """
    now_utc = as_of or datetime.now(timezone.utc)

    query = (
        db.query(DBSession)
        .filter(
            DBSession.end_at.is_not(None),
            DBSession.end_at <= now_utc,
            or_(
                DBSession.status != SessionStatus.CANCELLED,
                DBSession.status.is_(None),
            ),
            DBSession.recording_processing_status == RecordingProcessingStatus.PENDING,
            DBSession.tldv_meeting_id.is_not(None),
            DBSession.tldv_meeting_id != "",
        )
        .order_by(DBSession.end_at.asc(), DBSession.id.asc())
        .limit(limit)
    )

    return query.all()

def claim_next_pending_recording_candidate(
    db: Session,
    as_of: datetime | None = None,
    exclude_ids: set[UUID] | None = None,
) -> DBSession | None:
    """
    Atomically claim one pending recording candidate for processing.

    Uses PostgreSQL FOR UPDATE SKIP LOCKED so overlapping workers
    do not claim the same pending session at the same time.
    """
    now_utc = as_of or datetime.now(timezone.utc)
    exclude_ids = exclude_ids or set()

    query = (
        db.query(DBSession)
        .filter(
            DBSession.end_at.is_not(None),
            DBSession.end_at <= now_utc,
            or_(
                DBSession.status != SessionStatus.CANCELLED,
                DBSession.status.is_(None),
            ),
            DBSession.recording_processing_status
            == RecordingProcessingStatus.PENDING,
            DBSession.tldv_meeting_id.is_not(None),
            DBSession.tldv_meeting_id != "",
        )
    )

    if exclude_ids:
        query = query.filter(DBSession.id.notin_(exclude_ids))

    candidate = (
        query
        .order_by(DBSession.end_at.asc(), DBSession.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )

    if candidate is None:
        return None

    candidate.recording_processing_status = (
        RecordingProcessingStatus.PROCESSING
    )
    candidate.recording_processing_error = None

    db.commit()
    db.refresh(candidate)

    logger.info(
        "Claimed recording session %s for processing",
        candidate.id,
    )

    return candidate

def recover_stale_processing_recordings(
    db: Session,
    stale_after_hours: int = 6,
    as_of: datetime | None = None,
) -> int:
    """
    Recover sessions left stuck in PROCESSING after a worker crash.

    Only PROCESSING rows whose updated_at is older than the stale
    threshold are reset to PENDING so a later batch can retry them.
    """
    if stale_after_hours < 1:
        raise ValueError("stale_after_hours must be at least 1")

    now_utc = as_of or datetime.now(timezone.utc)
    stale_before = now_utc - timedelta(hours=stale_after_hours)

    recovered = (
        db.query(DBSession)
        .filter(
            DBSession.recording_processing_status
            == RecordingProcessingStatus.PROCESSING,
            DBSession.updated_at < stale_before,
        )
        .update(
            {
                DBSession.recording_processing_status:
                    RecordingProcessingStatus.PENDING,
                DBSession.recording_processing_error: None,
                DBSession.updated_at: now_utc,
            },
            synchronize_session=False,
        )
    )

    if recovered:
        db.commit()
        logger.warning(
            "Recovered %d stale recording processing session(s)",
            recovered,
        )

    return recovered


def process_pending_recordings(
    db: Session | None = None,
    limit: int = 10,
) -> RecordingBatchResult:
    """
    Batch process eligible pending recordings to Cloudflare R2 sequentially.

    If db is None, creates and manages a local session context.
    Catches per-session exceptions to guarantee failure isolation.
    """
    if db is None:
        with SessionLocal() as session_db:
            return _execute_batch_processing(session_db, limit=limit)
    return _execute_batch_processing(db, limit=limit)


def _execute_batch_processing(
    db: Session,
    limit: int = 10,
) -> RecordingBatchResult:
    recover_stale_processing_recordings(db)

    result = RecordingBatchResult()
    claimed_ids: set[UUID] = set()

    for _ in range(limit):
        # Atomically claim ONE pending session.
        # SKIP LOCKED prevents another worker from claiming
        # the same session at the same time.
        session = claim_next_pending_recording_candidate(
            db=db,
            exclude_ids=claimed_ids,
        )

        if session is None:
            break

        claimed_ids.add(session.id)
        result.scanned += 1
        result.processed += 1
        result.session_ids.append(session.id)

        logger.info(
            "Processing claimed recording for session %s "
            "(session_number=%s, end_at=%s)",
            session.id,
            session.session_number,
            session.end_at,
        )

        try:
            proc_result = process_session_recording_to_r2(
                session.id,
                db=db,
            )

            if proc_result.status == RecordingProcessingStatus.READY:
                result.ready += 1
                logger.info(
                    "Session %s recording successfully ready in R2",
                    session.id,
                )

            elif proc_result.status == RecordingProcessingStatus.PENDING:
                result.pending += 1
                logger.info(
                    "Session %s provider recording not ready yet "
                    "(will retry in next run)",
                    session.id,
                )

            elif proc_result.status == RecordingProcessingStatus.FAILED:
                result.failed += 1
                safe_err = _sanitize_job_error(
                    proc_result.error or "Processing failed"
                )
                result.errors[str(session.id)] = safe_err

                logger.warning(
                    "Session %s recording processing failed: %s",
                    session.id,
                    safe_err,
                )

            else:
                result.skipped += 1

        except Exception as exc:
            # Make sure the SQLAlchemy session can be used again.
            db.rollback()

            safe_err = _sanitize_job_error(str(exc))
            result.failed += 1
            result.errors[str(session.id)] = safe_err

            # A claimed session must not remain stuck in PROCESSING
            # after an unexpected exception.
            try:
                failed_session = db.get(DBSession, session.id)

                if (
                    failed_session is not None
                    and failed_session.recording_processing_status
                    == RecordingProcessingStatus.PROCESSING
                ):
                    failed_session.recording_processing_status = (
                        RecordingProcessingStatus.FAILED
                    )
                    failed_session.recording_processing_error = safe_err
                    db.commit()

            except Exception:
                db.rollback()
                logger.exception(
                    "Unable to persist FAILED state for session %s",
                    session.id,
                )

            logger.exception(
                "Unexpected failure processing recording for session %s: %s",
                session.id,
                safe_err,
            )

    logger.info(
        "Batch recording processing complete: "
        "scanned=%d, processed=%d, ready=%d, "
        "pending=%d, failed=%d, skipped=%d",
        result.scanned,
        result.processed,
        result.ready,
        result.pending,
        result.failed,
        result.skipped,
    )

    return result
