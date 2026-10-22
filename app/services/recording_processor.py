"""
Recording Processor Service

Orchestrates automatic processing of tl;dv meeting recordings to Cloudflare R2:
- Idempotency & recovery checks
- Provider readiness validation (leaving status pending if recording not ready)
- State transitions: PENDING -> PROCESSING -> READY (or FAILED on genuine failure)
- Media download, duration completeness validation, and FFmpeg faststart remuxing
- R2 upload and post-upload verification
- Safe cleanup of local temporary media files
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.session import RecordingProcessingStatus, Session as DBSession
from app.services.r2_storage import object_exists, upload_file
from app.services.tldv import (
    ensure_seekable_recording_file,
    get_session_lock,
    get_tldv_client,
)

logger = logging.getLogger(__name__)


@dataclass
class RecordingProcessingResult:
    session_id: UUID
    status: RecordingProcessingStatus
    object_key: str | None = None
    detail: str | None = None
    error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.status == RecordingProcessingStatus.READY

    @property
    def is_pending(self) -> bool:
        return self.status == RecordingProcessingStatus.PENDING

    @property
    def is_failed(self) -> bool:
        return self.status == RecordingProcessingStatus.FAILED


def _sanitize_error_message(err: str) -> str:
    """
    Sanitize error message to ensure no credentials, tokens, or signed URLs are stored in DB.
    """
    if not err:
        return "Unknown processing error"
    # Strip query parameters from any URLs (e.g. ?t=..., ?token=..., signature params)
    sanitized = re.sub(r"https?://[^\s\"']+", lambda m: m.group(0).split("?")[0], err)
    if len(sanitized) > 300:
        sanitized = sanitized[:300]
    return sanitized.strip()


def _cleanup_temp_files(session_id: UUID, seekable_file: str | None = None) -> None:
    """
    Safely delete temporary raw and seekable files created specifically for this session.
    Never delete unrelated or legacy files (such as session_2_seekable.mp4).
    """
    poc_dir = os.path.join(os.getcwd(), "tmp", "poc_media")
    specific_files = [
        os.path.join(poc_dir, f"session_{session_id}_raw.mp4"),
        os.path.join(poc_dir, f"session_{session_id}_seekable.tmp.mp4"),
        os.path.join(poc_dir, f"session_{session_id}_seekable.mp4"),
    ]
    if seekable_file and os.path.basename(seekable_file) == f"session_{session_id}_seekable.mp4":
        if seekable_file not in specific_files:
            specific_files.append(seekable_file)

    for fpath in specific_files:
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
                logger.debug("Cleaned up session temporary file: %s", fpath)
        except Exception as e:
            logger.warning("Failed to clean up temporary file %s: %s", fpath, e)


def process_session_recording_to_r2(
    session_id: UUID | str,
    db: Session | None = None,
) -> RecordingProcessingResult:
    """
    Process a session recording from tl;dv to Cloudflare R2 in an idempotent manner.
    Accepts session_id as either UUID or string representation.
    If no db session is provided, creates and manages a local session context.
    """
    if isinstance(session_id, str):
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            raise ValueError(f"Invalid session UUID: {session_id}")
    else:
        session_uuid = session_id

    if db is None:
        with SessionLocal() as session_db:
            return _execute_processing(session_uuid, session_db)
    return _execute_processing(session_uuid, db)


def _execute_processing(session_id: UUID, db: Session) -> RecordingProcessingResult:
    session = db.get(DBSession, session_id)
    if not session:
        raise ValueError(f"Session not found with id: {session_id}")

    object_key = f"recordings/{session.id}/recording.mp4"

    # Concurrency protection using per-session re-entrant lock
    with get_session_lock(str(session.id)):
        db.refresh(session)

        # 3. Idempotency Check:
        # If recording_processing_status == ready AND recording_r2_key exists AND R2 object exists:
        # return success immediately without downloading/remuxing again.
        if (
            session.recording_processing_status == RecordingProcessingStatus.READY
            and session.recording_r2_key
            and object_exists(session.recording_r2_key)
        ):
            logger.info(
                "Session %s recording is already READY in R2 (%s). Skipping.",
                session.id,
                session.recording_r2_key,
            )
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.READY,
                object_key=session.recording_r2_key,
                detail="Recording already processed and verified in R2.",
            )

        # 4. Recovery:
        # If R2 object already exists but DB state is not READY:
        # - set recording_r2_key
        # - set recording_processing_status = ready
        # - set recording_processed_at
        # - clear recording_processing_error
        # - commit
        # - do not re-download
        if object_exists(object_key):
            logger.info(
                "R2 object already exists at %s for session %s. Repairing DB state to ready.",
                object_key,
                session.id,
            )
            session.recording_r2_key = object_key
            session.recording_processing_status = RecordingProcessingStatus.READY
            session.recording_processed_at = datetime.now(timezone.utc)
            session.recording_processing_error = None
            db.commit()
            db.refresh(session)
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.READY,
                object_key=object_key,
                detail="Repaired DB state to ready: object already exists in R2.",
            )

        # 5. tl;dv NOT READY is NOT a failure.
        # If tl;dv has not finished processing or no provider recording is available yet:
        # - leave/set recording_processing_status = pending
        # - do not set failed
        # - return a result indicating provider_not_ready / pending
        if not session.tldv_meeting_id:
            logger.info(
                "Session %s has no tldv_meeting_id configured yet. Leaving status as pending.",
                session.id,
            )
            if session.recording_processing_status != RecordingProcessingStatus.PENDING:
                session.recording_processing_status = RecordingProcessingStatus.PENDING
                db.commit()
                db.refresh(session)
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.PENDING,
                detail="Provider recording not ready: no tldv_meeting_id configured.",
            )

        client = get_tldv_client()
        if client is None:
            logger.info(
                "tl;dv client is not available. Leaving status as pending for session %s.",
                session.id,
            )
            if session.recording_processing_status != RecordingProcessingStatus.PENDING:
                session.recording_processing_status = RecordingProcessingStatus.PENDING
                db.commit()
                db.refresh(session)
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.PENDING,
                detail="Provider recording not ready: tl;dv client is unavailable.",
            )

        try:
            playback_data = client.get_recording_playback(session.tldv_meeting_id)
        except Exception as exc:
            logger.warning(
                "Error checking tl;dv playback for session %s: %s",
                session.id,
                exc,
            )
            if session.recording_processing_status != RecordingProcessingStatus.PENDING:
                session.recording_processing_status = RecordingProcessingStatus.PENDING
                db.commit()
                db.refresh(session)
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.PENDING,
                detail="Provider recording not ready: error checking provider playback.",
            )

        if playback_data.status != "ready" or not playback_data.playback_url:
            logger.info(
                "Provider recording for session %s is not ready (status=%s). Leaving status as pending.",
                session.id,
                playback_data.status,
            )
            if session.recording_processing_status != RecordingProcessingStatus.PENDING:
                session.recording_processing_status = RecordingProcessingStatus.PENDING
                db.commit()
                db.refresh(session)
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.PENDING,
                detail=f"Provider recording not ready (provider status: {playback_data.status}).",
            )

        # 6. Once a provider recording is actually available:
        # set:
        # recording_processing_status = processing
        # recording_processing_error = null
        # commit before starting the long media processing operation.
        session.recording_processing_status = RecordingProcessingStatus.PROCESSING
        session.recording_processing_error = None
        db.commit()
        db.refresh(session)

        # 7-10. Media download, FFmpeg remux, R2 upload, verification, and completion
        seekable_file: str | None = None
        try:
            # 7. Prepare seekable MP4 via proven tl;dv download & ffmpeg remux pipeline
            seekable_file = ensure_seekable_recording_file(session)

            # 8. Upload final seekable MP4 to R2
            upload_file(
                local_path=seekable_file,
                object_key=object_key,
                content_type="video/mp4",
            )

            # 9. Verify R2 object exists after upload
            if not object_exists(object_key):
                raise RuntimeError(
                    f"R2 object verification failed for object key: {object_key}"
                )

            # 10. After successful upload:
            session.recording_processing_status = RecordingProcessingStatus.READY
            session.recording_r2_key = object_key
            session.recording_processed_at = datetime.now(timezone.utc)
            session.recording_processing_error = None
            db.commit()
            db.refresh(session)

            logger.info(
                "Successfully processed session %s recording to R2 (%s).",
                session.id,
                object_key,
            )
            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.READY,
                object_key=object_key,
                detail="Successfully processed and uploaded to R2.",
            )

        except Exception as exc:
            # 12. Failure handling
            safe_error = _sanitize_error_message(str(exc))
            logger.exception(
                "Recording processing failed for session %s: %s",
                session.id,
                safe_error,
            )
            session.recording_processing_status = RecordingProcessingStatus.FAILED
            session.recording_processing_error = safe_error
            try:
                db.commit()
                db.refresh(session)
            except Exception as commit_exc:
                logger.exception(
                    "Failed to commit FAILED status for session %s: %s",
                    session.id,
                    commit_exc,
                )

            return RecordingProcessingResult(
                session_id=session.id,
                status=RecordingProcessingStatus.FAILED,
                error=safe_error,
            )

        finally:
            # 11. Cleanup temporary local files created specifically for processing
            _cleanup_temp_files(session.id, seekable_file)
