"""
tl;dv Integration Service.

Provides:
- TLDVClient Protocol
- MockTLDVClient for local testing and demonstration
- HttpTLDVClient for real API interaction via pasta.tldv.io
- Session-level helper functions for Fellow Portal recording & transcript retrieval
"""

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import subprocess
import threading
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.session import Session as DBSession
from app.models.user import User
from app.repositories.discover import get_session_by_id_for_cohort
from app.repositories.enrollment import get_active_enrollment_for_user
from app.schemas.session_media import (
    SessionRecordingPlaybackResponse,
    SessionTranscriptResponse,
    TranscriptSegment,
)
from app.services.discover import require_session_unlocked
from app.services.r2_storage import (
    generate_presigned_get_url,
    object_exists,
)

logger = logging.getLogger(__name__)
def _sanitize_media_log(value: str) -> str:
    """
    Remove query strings from URLs before writing provider/FFmpeg output to logs.
    """
    if not value:
        return ""

    sanitized = re.sub(
        r"https?://[^\s\"']+",
        lambda match: match.group(0).split("?")[0],
        value,
    )

    return sanitized[-500:]

# ---------------------------------------------------------------------------
# Data Models for Provider Layer
# ---------------------------------------------------------------------------


class TLDVPlaybackData(BaseModel):
    status: str
    playback_url: str | None = None
    duration_seconds: int | None = None
    expires_at: datetime | None = None


class TLDVTranscriptData(BaseModel):
    status: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    full_text: str | None = None


# ---------------------------------------------------------------------------
# Client Protocol and Implementations
# ---------------------------------------------------------------------------


class TLDVClient(Protocol):
    def get_recording_playback(self, meeting_id: str) -> TLDVPlaybackData:
        ...

    def get_transcript(self, meeting_id: str) -> TLDVTranscriptData:
        ...


class MockTLDVClient:
    """
    Mock client for local development and test automation.
    Activated only when settings.tldv_mock_mode is explicitly True.
    """

    def get_recording_playback(self, meeting_id: str) -> TLDVPlaybackData:
        return TLDVPlaybackData(
            status="ready",
            # Standard public demo MP4 stream for browser testing
            playback_url="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
            duration_seconds=596,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        )

    def get_transcript(self, meeting_id: str) -> TLDVTranscriptData:
        segments = [
            TranscriptSegment(
                speaker="Programme Lead",
                text="Welcome everyone to today's DLIF fellowship live session.",
                start_time=0.0,
                end_time=14.5,
            ),
            TranscriptSegment(
                speaker="Fellow Samantha",
                text="Hi! Excited to review our company challenge milestones.",
                start_time=15.0,
                end_time=29.2,
            ),

            TranscriptSegment(
                speaker="Programme Lead",
                text="Let's dive into the research deliverables and strategic questions.",
                start_time=30.0,
                end_time=48.0,
            ),
        ]
        full_text = " ".join(s.text for s in segments)
        return TLDVTranscriptData(
            status="ready",
            segments=segments,
            full_text=full_text,
        )


class HttpTLDVClient:
    """
    Real HTTP client communicating with the tl;dv v1alpha1 REST API.
    Never exposes API keys or sensitive URLs in logs.
    """

    def __init__(self, api_key: str, base_url: str = "https://pasta.tldv.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "DLIF-Platform-Backend/1.0",
        }

    def get_recording_playback(self, meeting_id: str) -> TLDVPlaybackData:
        url = f"{self.base_url}/v1alpha1/meetings/{meeting_id}/download"
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                response = client.get(url, headers=self._headers())

                # The tl;dv API issues a 302/307 redirect to the signed video file URL
                if response.status_code in (301, 302, 303, 307, 308):
                    signed_url = response.headers.get("location")
                    if signed_url:
                        return TLDVPlaybackData(
                            status="ready",
                            playback_url=signed_url,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                        )

                if response.status_code == 200:
                    payload = response.json()
                    signed_url = payload.get("url") or payload.get("downloadUrl")
                    if signed_url:
                        return TLDVPlaybackData(
                            status="ready",
                            playback_url=signed_url,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
                        )

                if response.status_code == 404:
                    return TLDVPlaybackData(status="not_available")

                if response.status_code == 422:
                    return TLDVPlaybackData(status="processing")

                logger.warning(
                    "Unexpected status code from tl;dv download endpoint: %d",
                    response.status_code,
                )
                return TLDVPlaybackData(status="not_available")

        except httpx.RequestError as exc:
            logger.error("HTTP request error contacting tl;dv recording endpoint: %s", type(exc).__name__)
            return TLDVPlaybackData(status="not_available")

    def get_transcript(self, meeting_id: str) -> TLDVTranscriptData:
        url = f"{self.base_url}/v1alpha1/meetings/{meeting_id}/transcript"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=self._headers())

                if response.status_code == 200:
                    payload = response.json()
                    raw_segments = (
                        payload.get("data")
                        or payload.get("segments")
                        or payload.get("transcript")
                        or []
                    )
                    segments: list[TranscriptSegment] = []

                    if isinstance(raw_segments, list):
                        for item in raw_segments:
                            if isinstance(item, dict):
                                speaker = item.get("speaker") or "Speaker"
                                text = item.get("text") or ""
                                start_time = float(item.get("startTime") or item.get("start_time") or 0.0)
                                end_time = float(item.get("endTime") or item.get("end_time") or start_time)
                                segments.append(
                                    TranscriptSegment(
                                        speaker=speaker,
                                        text=text,
                                        start_time=start_time,
                                        end_time=end_time,
                                    )
                                )

                    full_text = " ".join(s.text for s in segments) if segments else None
                    return TLDVTranscriptData(
                        status="ready" if segments else "processing",
                        segments=segments,
                        full_text=full_text,
                    )

                if response.status_code == 404:
                    return TLDVTranscriptData(status="not_available")

                if response.status_code == 422:
                    return TLDVTranscriptData(status="processing")

                logger.warning(
                    "Unexpected status code from tl;dv transcript endpoint: %d",
                    response.status_code,
                )
                return TLDVTranscriptData(status="not_available")

        except httpx.RequestError as exc:
            logger.error("HTTP request error contacting tl;dv transcript endpoint: %s", type(exc).__name__)
            return TLDVTranscriptData(status="not_available")


def get_tldv_client() -> TLDVClient | None:
    """
    Factory resolving the active tl;dv client.
    - If mock mode is explicitly enabled: MockTLDVClient
    - If API key is present: HttpTLDVClient
    - Otherwise: None (Never automatically defaults to mock mode).
    """
    if settings.tldv_mock_mode:
        return MockTLDVClient()

    if settings.tldv_api_key:
        return HttpTLDVClient(
            api_key=settings.tldv_api_key,
            base_url=settings.tldv_api_base_url,
        )

    return None


# ---------------------------------------------------------------------------

# Fellow Portal Service Methods
# ---------------------------------------------------------------------------


def _authorize_fellow_session_access(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> DBSession:
    """
    Verify fellow authentication, active cohort enrollment, cross-cohort isolation,
    and session unlock permissions.
    """
    enrollment = get_active_enrollment_for_user(db, current_user.id)
    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active cohort enrollment found for current Fellow.",
        )

    session = get_session_by_id_for_cohort(db, session_id, enrollment.cohort_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    require_session_unlocked(session)
    return session


def get_session_recording_playback_for_fellow(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> SessionRecordingPlaybackResponse:
    """
    Retrieve in-portal video playback information for an authorized fellow.
    """
    session = _authorize_fellow_session_access(db, current_user, session_id)

    # Prefer Cloudflare R2 when the processed recording exists there.
    object_key = f"recordings/{session.id}/recording.mp4"
    try:
        if object_exists(object_key):
            playback_url = generate_presigned_get_url(object_key)
            expiry = datetime.now(timezone.utc) + timedelta(
                seconds=settings.r2_presigned_url_expiry_seconds,
            )
            return SessionRecordingPlaybackResponse(
                session_id=session.id,
                has_recording=True,
                status="ready",
                playback_url=playback_url,
                duration_seconds=session.recording_duration_seconds,
                expires_at=expiry,
                provider="r2",
            )
    except Exception:
        logger.exception(
            "R2 playback lookup failed for session %s; falling back to provider",
            session.id,
        )

    # 1. tl;dv Meeting ID workflow
    if session.tldv_meeting_id:
        client = get_tldv_client()
        if client is not None:
            data = client.get_recording_playback(session.tldv_meeting_id)
            if data.status == "ready" and data.playback_url:
                return SessionRecordingPlaybackResponse(
                    session_id=session.id,
                    has_recording=True,
                    status="ready",
                    playback_url=data.playback_url,
                    duration_seconds=data.duration_seconds or session.recording_duration_seconds,
                    expires_at=data.expires_at,
                    provider="tldv",
                )
            return SessionRecordingPlaybackResponse(
                session_id=session.id,
                has_recording=False,
                status=data.status,
                playback_url=None,
                duration_seconds=session.recording_duration_seconds,
                expires_at=None,
                provider="tldv",
            )

        # No client available (no API key and mock mode is False)
        # Fallback to legacy URL if present
        if session.recording_url:
            return SessionRecordingPlaybackResponse(
                session_id=session.id,
                has_recording=True,
                status="ready",
                playback_url=session.recording_url,
                duration_seconds=session.recording_duration_seconds,
                expires_at=None,
                provider="legacy",
            )

        return SessionRecordingPlaybackResponse(
            session_id=session.id,
            has_recording=False,
            status=session.recording_status or "not_available",
            playback_url=None,
            duration_seconds=session.recording_duration_seconds,
            expires_at=None,
            provider="tldv",
        )

    # 2. Legacy recording_url workflow
    if session.recording_url:
        return SessionRecordingPlaybackResponse(
            session_id=session.id,
            has_recording=True,
            status="ready",
            playback_url=session.recording_url,
            duration_seconds=session.recording_duration_seconds,
            expires_at=None,
            provider="legacy",
        )

    # 3. Not available
    return SessionRecordingPlaybackResponse(
        session_id=session.id,
        has_recording=False,
        status="not_available",
        playback_url=None,
        duration_seconds=None,
        expires_at=None,
        provider="none",
    )


def get_session_transcript_for_fellow(
    db: Session,
    current_user: User,
    session_id: UUID,
) -> SessionTranscriptResponse:
    """
    Retrieve structured in-portal transcript segments for an authorized fellow.
    """
    session = _authorize_fellow_session_access(db, current_user, session_id)

    # 1. Check cached database transcript data first
    if session.transcript_data:
        raw = session.transcript_data
        segments: list[TranscriptSegment] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    segments.append(
                        TranscriptSegment(
                            speaker=item.get("speaker") or "Speaker",
                            text=item.get("text") or "",
                            start_time=float(item.get("start_time") or item.get("startTime") or 0.0),
                            end_time=float(item.get("end_time") or item.get("endTime") or 0.0),
                        )
                    )
        elif isinstance(raw, dict) and "segments" in raw:
            for item in raw["segments"]:
                if isinstance(item, dict):
                    segments.append(
                        TranscriptSegment(
                            speaker=item.get("speaker") or "Speaker",
                            text=item.get("text") or "",
                            start_time=float(item.get("start_time") or item.get("startTime") or 0.0),
                            end_time=float(item.get("end_time") or item.get("endTime") or 0.0),
                        )
                    )

        full_text = " ".join(s.text for s in segments) if segments else None
        return SessionTranscriptResponse(
            session_id=session.id,
            has_transcript=bool(segments),
            status="ready" if segments else "not_available",
            segments=segments,
            full_text=full_text,
            provider="tldv",
        )

    # 2. Check tl;dv provider workflow
    if session.tldv_meeting_id:
        client = get_tldv_client()
        if client is not None:
            data = client.get_transcript(session.tldv_meeting_id)
            if data.status == "ready" and data.segments:
                return SessionTranscriptResponse(
                    session_id=session.id,
                    has_transcript=True,
                    status="ready",
                    segments=data.segments,
                    full_text=data.full_text,
                    provider="tldv",
                )
            return SessionTranscriptResponse(
                session_id=session.id,
                has_transcript=False,
                status=data.status,
                segments=[],
                full_text=None,
                provider="tldv",
            )

        # No client available (no API key and mock mode is False)
        if session.transcript_url:
            return SessionTranscriptResponse(
                session_id=session.id,
                has_transcript=True,
                status="ready",
                segments=[],
                full_text=f"Transcript available at {session.transcript_url}",
                provider="legacy",
            )

        return SessionTranscriptResponse(
            session_id=session.id,
            has_transcript=False,
            status=session.recording_status or "not_available",
            segments=[],
            full_text=None,
            provider="tldv",
        )

    # 3. Legacy transcript_url workflow (when session has no tldv_meeting_id)
    if session.transcript_url:
        return SessionTranscriptResponse(
            session_id=session.id,
            has_transcript=True,
            status="ready",
            segments=[],
            full_text=f"Transcript available at {session.transcript_url}",
            provider="legacy",
        )

    # 4. Not available
    return SessionTranscriptResponse(
        session_id=session.id,
        has_transcript=False,
        status="not_available",
        segments=[],
        full_text=None,
        provider="none",
    )


# ---------------------------------------------------------------------------
# Seekable Stream Remuxing & Chunking Infrastructure
# ---------------------------------------------------------------------------

_locks_mutex = threading.Lock()
_session_remux_locks: dict[str, threading.RLock] = {}

def _get_session_lock(session_id: str) -> threading.RLock:
    with _locks_mutex:
        if session_id not in _session_remux_locks:
            _session_remux_locks[session_id] = threading.RLock()
        return _session_remux_locks[session_id]


get_session_lock = _get_session_lock
def _ensure_seekable_recording_file(session: DBSession) -> str:
    """
    Locate or dynamically prepare a seekable MP4 file for any session.
    1. Checks session-specific candidate paths in tmp/poc_media.
    2. If valid seekable file exists, returns immediately (no redundant downloads).
    3. If missing, downloads the raw stream from tl;dv using client.get_recording_playback.
    4. Remuxes using FFmpeg stream copy (-c copy -movflags +faststart) for proper byte-range seeking.
    5. Caches output as session_{session.id}_seekable.mp4 and removes intermediate raw file.
    """
    poc_dir = os.path.join(os.getcwd(), "tmp", "poc_media")
    os.makedirs(poc_dir, exist_ok=True)

    canonical_seekable_path = os.path.join(
        poc_dir,
        f"session_{session.id}_seekable.mp4",
    )

    candidate_paths = [
        canonical_seekable_path,
    ]

    for p in candidate_paths:
        if p and os.path.isfile(p) and os.path.getsize(p) > 0:
            return p

    # Verify session has recording configured
    if not session.tldv_meeting_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No recording available for this session.",
        )

    # Acquire per-session lock to prevent concurrent duplicate download/remux jobs
    with _get_session_lock(str(session.id)):
        # Double-check inside lock
        for p in candidate_paths:
            if p and os.path.isfile(p) and os.path.getsize(p) > 0:
                return p

        client = get_tldv_client()
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="tl;dv integration client is not available.",
            )

        playback_data = client.get_recording_playback(session.tldv_meeting_id)
        if playback_data.status != "ready" or not playback_data.playback_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Recording is not ready (status: {playback_data.status}).",
            )

        download_url = playback_data.playback_url
        raw_path = os.path.join(poc_dir, f"session_{session.id}_raw.mp4")
        tmp_seekable_path = os.path.join(poc_dir, f"session_{session.id}_seekable.tmp.mp4")

        # 1. Download raw recording from provider using resilient streaming.
        #
        # Provider characteristics & Completeness Validation:
        # - tl;dv /download issues a 302 redirect to puttanesca-v0.tldv.io/v1/download.mp4?t=...
        # - The provider returns Transfer-Encoding: chunked without Content-Length.
        # - The provider does NOT support byte ranges (returns HTTP 200 to all requests).
        # - The remote host closes connections intermittently before the complete file is transferred.
        # - Merely parsing as an MP4 via ffprobe does NOT constitute download completion.
        #   Duration completeness is verified against the real recording metadata duration (~1:50:49 for Session 1).
        # - FFmpeg stream capture with reconnect flags (-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 5)
        #   survives transport drops and captures the full continuous stream into raw_path.
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
        except Exception:
            pass

        def _probe_duration(filepath: str) -> float | None:
            try:
                res = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1",
                        filepath,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if "duration=" in line:
                            val = line.split("=", 1)[1].strip()
                            return float(val)
            except Exception:
                pass
            return None

        # Fetch expected meeting duration from metadata
        expected_duration_seconds: float | None = float(session.recording_duration_seconds) if session.recording_duration_seconds else None
        if not expected_duration_seconds:
            try:
                meta_headers = {
                    "x-api-key": settings.tldv_api_key.strip(),
                    "Accept": "application/json",
                }
                with httpx.Client(timeout=10.0) as meta_client:
                    meeting_meta_url = f"{settings.tldv_api_base_url.rstrip('/')}/v1alpha1/meetings/{session.tldv_meeting_id}"
                    meta_resp = meta_client.get(meeting_meta_url, headers=meta_headers)
                    if meta_resp.status_code == 200:
                        m_dur = meta_resp.json().get("duration")
                        if m_dur:
                            expected_duration_seconds = float(m_dur)
            except Exception as e:
                logger.warning("Could not query meeting metadata duration: %s", e)

        _MAX_DOWNLOAD_RETRIES = 5
        _download_succeeded = False

        for _attempt in range(_MAX_DOWNLOAD_RETRIES):
            if _attempt > 0:
                logger.info(
                    "Retrying full recording stream capture for session %s (attempt %d/%d)",
                    session.id, _attempt + 1, _MAX_DOWNLOAD_RETRIES,
                )
                playback_data = client.get_recording_playback(session.tldv_meeting_id)
                if playback_data.status != "ready" or not playback_data.playback_url:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Recording became unavailable during download.",
                    )
                download_url = playback_data.playback_url

            # Capture complete stream into raw_path using reconnecting stream transport
            cmd_capture = [
                "ffmpeg", "-y",
                "-reconnect", "1",
                "-reconnect_at_eof", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-i", download_url,
                "-c", "copy",
                raw_path,
            ]

            try:
                res_cap = subprocess.run(cmd_capture, capture_output=True, text=True, timeout=900)
                if res_cap.returncode != 0:
                    logger.warning(
                        "Stream capture returned non-zero %d: %s",
                        res_cap.returncode,
                        _sanitize_media_log(res_cap.stderr),
                    )
            except subprocess.TimeoutExpired:
                logger.warning("Stream capture timed out on attempt %d", _attempt + 1)
            except Exception as exc:
                logger.warning(
                    "Stream capture failed with exception: %s",
                    _sanitize_media_log(str(exc)),
                )
            if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
                probed = _probe_duration(raw_path)
                logger.info(
                    "Session %s raw capture size: %d bytes, probed duration: %s (expected: %s)",
                    session.id, os.path.getsize(raw_path), probed, expected_duration_seconds,
                )
                # Verify duration completeness: must reach at least 95% of expected duration if known
                if probed and (not expected_duration_seconds or probed >= (expected_duration_seconds * 0.95)):
                    _download_succeeded = True
                    break
                else:
                    logger.warning(
                        "Session %s raw capture incomplete (duration %s < expected %s) - restarting capture",
                        session.id, probed, expected_duration_seconds,
                    )
                    try:
                        os.remove(raw_path)
                    except Exception:
                        pass

        if not _download_succeeded:
            if os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except Exception:
                    pass
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to download complete recording file from provider.",
            )

        # 2. Remux complete raw file into seekable MP4 with FFmpeg (+faststart)
        cmd_remux = [
            "ffmpeg",
            "-y",
            "-i", raw_path,
            "-c", "copy",
            "-movflags", "+faststart",
            canonical_seekable_path,
        ]
        try:
            res_remux = subprocess.run(cmd_remux, capture_output=True, text=True)
            if res_remux.returncode != 0:
                logger.error(
                    "FFmpeg remuxing failed for session %s: %s",
                    session.id,
                    _sanitize_media_log(res_remux.stderr),
                )
                if os.path.exists(canonical_seekable_path):
                    try:
                        os.remove(canonical_seekable_path)
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to remux recording into seekable format.",
                )
        finally:
            if os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except Exception:
                    pass
        return canonical_seekable_path


ensure_seekable_recording_file = _ensure_seekable_recording_file


def stream_seekable_recording_for_fellow(
    db: Session,
    current_user: User,
    session_id: UUID,
    range_header: str | None = None,
) -> Response:
    """
    HTTP 206 Partial Content range streaming endpoint for any DLIF session recording.
    Enforces fellow cohort authorization and session unlock rules.
    """
    session = _authorize_fellow_session_access(db, current_user, session_id)

    # Prefer Cloudflare R2 when the processed recording exists there.
    object_key = f"recordings/{session.id}/recording.mp4"

    try:
        if object_exists(object_key):
            playback_url = generate_presigned_get_url(object_key)

            return RedirectResponse(
                url=playback_url,
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            )
    except Exception:
        logger.exception(
            "R2 playback lookup failed for session %s; falling back to local stream",
            session.id,
        )

    # Existing local/tl;dv fallback.
    file_path = _ensure_seekable_recording_file(session)

    file_size = os.path.getsize(file_path)

    # If no Range header requested, serve full content
    if not range_header or not range_header.strip():
        def full_content_generator():
            with open(file_path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    yield chunk

        return StreamingResponse(
            full_content_generator(),
            status_code=status.HTTP_200_OK,
            headers={
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
                "Content-Type": "video/mp4",
            },
        )

    # Parse and validate HTTP Range header: bytes=start-end
    match = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not match:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_str, end_str = match.groups()
    if not start_str and not end_str:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    if not start_str:
        # Suffix range: bytes=-N (last N bytes)
        suffix_len = int(end_str)
        if suffix_len <= 0:
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    else:
        start = int(start_str)
        if end_str:
            end = int(end_str)
        else:
            end = file_size - 1

    if start >= file_size or start > end or start < 0:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    end = min(end, file_size - 1)
    content_length = (end - start) + 1

    def range_content_generator(start_pos: int, total_to_read: int):
        with open(file_path, "rb") as f:
            f.seek(start_pos)
            bytes_remaining = total_to_read
            chunk_size = 256 * 1024
            while bytes_remaining > 0:
                chunk = f.read(min(chunk_size, bytes_remaining))
                if not chunk:
                    break
                bytes_remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        range_content_generator(start, content_length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
            "Accept-Ranges": "bytes",
            "Content-Type": "video/mp4",
        },
    )




def stream_poc_seekable_recording_for_fellow(
    db: Session,
    current_user: User,
    session_id: UUID,
    range_header: str | None = None,
) -> Response:
    """
    Backwards-compatible alias for the POC route (delegates to stream_seekable_recording_for_fellow).
    """
    return stream_seekable_recording_for_fellow(
        db,
        current_user,
        session_id,
        range_header=range_header,
    )
