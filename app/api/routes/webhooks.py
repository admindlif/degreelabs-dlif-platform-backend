"""
tl;dv Webhook Route

Receives tl;dv event notifications (MeetingReady, TranscriptReady) and
triggers asynchronous recording processing for the matching DLIF session.

Security model:
- Verifies the incoming X-TLDV-Signature header (HMAC-SHA256 over raw body)
  using the shared secret configured via TLDV_WEBHOOK_SECRET env var.
- If no webhook secret is configured, all requests are rejected (fail-closed).
- Responds immediately with HTTP 200; processing runs in a background thread
  so tl;dv is not kept waiting.
- Never logs meeting IDs, session IDs, signed URLs, or raw payloads.

Idempotency:
- Processing is delegated to process_session_recording_to_r2(), which already
  checks for an existing R2 object and skips re-download.
- Duplicate MeetingReady events for the same meeting are therefore harmless.
"""

import hashlib
import hmac
import logging
import threading
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.session import RecordingProcessingStatus, Session as DBSession
from app.services.recording_processor import process_session_recording_to_r2

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_SUPPORTED_EVENTS = {"MeetingReady", "EventMeetingReady"}
_TRANSCRIPT_EVENTS = {"TranscriptReady", "EventTranscriptReady"}


def _verify_webhook_auth(
    raw_body: bytes,
    signature_header: str | None,
    secret_header: str | None,
    auth_header: str | None,
) -> bool:
    """
    Validate the webhook request authenticity against configured TLDV_WEBHOOK_SECRET.
    Supports:
    1. Direct secret matching via X-Webhook-Secret, x-api-key, or Authorization: Bearer <token>
    2. HMAC-SHA256 signature via X-TLDV-Signature (sha256=<hex> or raw hex)
    Fail-closed: Returns False if no secret is configured or no valid header is supplied.
    """
    secret = settings.tldv_webhook_secret
    if not secret:
        logger.error(
            "tl;dv webhook received but TLDV_WEBHOOK_SECRET is not configured. "
            "Rejecting request."
        )
        return False

    # 1. Direct header match (tl;dv Custom Header in UI: X-Webhook-Secret or x-api-key)
    if secret_header and hmac.compare_digest(secret_header.strip(), secret):
        return True

    # 2. Authorization: Bearer <token>
    if auth_header:
        token = auth_header.removeprefix("Bearer ").strip()
        if hmac.compare_digest(token, secret):
            return True

    # 3. HMAC-SHA256 signature
    if signature_header:
        expected = hmac.new(
            secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        candidate = signature_header.removeprefix("sha256=").strip()
        if hmac.compare_digest(expected, candidate):
            return True

    return False


def _find_session_by_meeting_id(meeting_id: str) -> DBSession | None:
    """Look up a DLIF session by tldv_meeting_id. Returns None if not found."""
    with SessionLocal() as db:
        return (
            db.query(DBSession)
            .filter(
                DBSession.tldv_meeting_id == meeting_id,
            )
            .first()
        )


def _trigger_processing(session_id: UUID) -> None:
    """
    Run recording processing in a background thread.
    Errors are logged but never propagate to the HTTP response.
    """
    try:
        result = process_session_recording_to_r2(session_id)
        if result.is_ready:
            logger.info(
                "Webhook-triggered processing complete: session %s is READY in R2.",
                session_id,
            )
        elif result.is_pending:
            logger.info(
                "Webhook-triggered processing: session %s recording not yet ready "
                "in tl;dv (will be retried by next scheduled run).",
                session_id,
            )
        else:
            logger.warning(
                "Webhook-triggered processing: session %s finished with status %s.",
                session_id,
                result.status,
            )
    except Exception:
        logger.exception(
            "Unexpected error during webhook-triggered processing for session %s.",
            session_id,
        )


def _save_transcript_data(meeting_id: str, transcript_data: list | dict) -> bool:
    """Save transcript segments directly into session.transcript_data."""
    try:
        with SessionLocal() as db:
            session = db.query(DBSession).filter(DBSession.tldv_meeting_id == meeting_id).first()
            if session:
                session.transcript_data = transcript_data
                db.commit()
                logger.info("Saved transcript data for session %s (meeting %s)", session.id, meeting_id)
                return True
    except Exception as exc:
        logger.exception("Failed to save transcript for meeting %s: %s", meeting_id, exc)
    return False


@router.post(
    "/tldv",
    status_code=status.HTTP_200_OK,
    summary="Receive tl;dv meeting-ready webhook events",
)
async def receive_tldv_webhook(
    request: Request,
    x_tldv_signature: str | None = Header(None, alias="X-TLDV-Signature"),
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    x_api_key: str | None = Header(None, alias="x-api-key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict:
    """
    Handle incoming tl;dv webhook events.

    Supported events:
    - MeetingReady: triggers R2 recording processing
    - TranscriptReady: persists transcript segments to PostgreSQL

    Always responds within 5 seconds. Processing runs asynchronously.
    """
    raw_body = await request.body()

    # --- Authentication verification (fail-closed) ---
    secret_header = x_webhook_secret or x_api_key
    if not _verify_webhook_auth(raw_body, x_tldv_signature, secret_header, authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature/secret.",
        )

    # --- Parse payload ---
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload is not valid JSON.",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be a JSON object.",
        )

    event_type = payload.get("event") or payload.get("type") or ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    # Extract meeting ID from nested data or top-level
    meeting_id = str(
        data.get("id")
        or data.get("meetingId")
        or payload.get("meetingId")
        or payload.get("id")
        or ""
    ).strip()

    # --- Transcript events: persist transcript data ---
    if event_type in _TRANSCRIPT_EVENTS:
        raw_segments = data.get("data") or data.get("segments") or []
        if meeting_id and raw_segments:
            saved = _save_transcript_data(meeting_id, raw_segments)
            return {"status": "accepted", "action": "transcript_saved" if saved else "no_match"}
        return {"status": "acknowledged", "action": "none"}

    # --- Only handle MeetingReady ---
    if event_type not in _SUPPORTED_EVENTS:
        logger.info(
            "tl;dv webhook event type '%s' is not handled; acknowledging.",
            event_type,
        )
        return {"status": "acknowledged", "action": "none"}

    if not meeting_id:
        logger.warning("tl;dv MeetingReady webhook received with no meeting ID.")
        return {"status": "acknowledged", "action": "none"}

    # --- Find matching DLIF session ---
    session = _find_session_by_meeting_id(meeting_id)


    if session is None:
        logger.info(
            "tl;dv MeetingReady webhook: no DLIF session matched for this meeting "
            "(meeting may not be a DLIF session — ignoring)."
        )
        return {"status": "acknowledged", "action": "no_match"}

    session_id = session.id

    # Skip if already READY (idempotent guard at webhook layer)
    if session.recording_processing_status == RecordingProcessingStatus.READY:
        logger.info(
            "tl;dv MeetingReady webhook: session is already READY — skipping."
        )
        return {"status": "acknowledged", "action": "already_ready"}

    logger.info(
        "tl;dv MeetingReady webhook: triggering async processing for session %s.",
        session_id,
    )

    # Respond immediately; process in background thread
    thread = threading.Thread(
        target=_trigger_processing,
        args=(session_id,),
        daemon=True,
        name=f"tldv-webhook-{session_id}",
    )
    thread.start()

    return {"status": "accepted", "action": "processing_queued"}
