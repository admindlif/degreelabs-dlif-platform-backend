from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.cohort import Cohort
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.schemas.admin_crud import SessionCreate, SessionResponse, SessionUpdate
from app.schemas.discover import SessionDetailResponse, SessionSummary
from app.schemas.session_media import (
    SessionRecordingPlaybackResponse,
    SessionTranscriptResponse,
    TranscriptSegment,
)


def test_tldv_config_defaults():
    """Verify that tl;dv settings are properly loaded with secure defaults."""
    # Instantiating a fresh Settings object to verify field defaults
    fresh_settings = Settings(
        _env_file=None,
        database_url="postgresql+pg8000://user:pass@localhost:5432/db",
        secret_key="a" * 32,
        frontend_base_url="http://localhost:3000",
        email_from_address="test@example.com",
        email_from_name="Test",
    )
    assert fresh_settings.tldv_api_key is None
    assert fresh_settings.tldv_api_base_url == "https://pasta.tldv.io"
    # Crucial requirement: mock mode MUST default to False and NOT auto-enable
    assert fresh_settings.tldv_mock_mode is False


def test_session_media_schemas():
    """Verify session_media schemas serialize properly."""
    segment = TranscriptSegment(
        speaker="Speaker 1",
        text="Welcome to the fellowship.",
        start_time=0.0,
        end_time=5.2,
    )
    assert segment.speaker == "Speaker 1"
    assert segment.start_time == 0.0
    assert segment.end_time == 5.2

    session_id = uuid4()
    playback_resp = SessionRecordingPlaybackResponse(
        session_id=session_id,
        has_recording=True,
        status="ready",
        playback_url="https://signed-stream.example.com/video.mp4",
        duration_seconds=3600,
        expires_at=datetime.now(timezone.utc),
        provider="tldv",
    )
    assert playback_resp.session_id == session_id
    assert playback_resp.status == "ready"
    assert playback_resp.duration_seconds == 3600

    transcript_resp = SessionTranscriptResponse(
        session_id=session_id,
        has_transcript=True,
        status="ready",
        segments=[segment],
        full_text="Welcome to the fellowship.",
        provider="tldv",
    )
    assert transcript_resp.has_transcript is True
    assert len(transcript_resp.segments) == 1
    assert transcript_resp.full_text == "Welcome to the fellowship."


def test_admin_session_schemas_tldv_support():
    """Verify Admin session schemas accept optional tldv_meeting_id."""
    cohort_id = uuid4()
    phase_id = uuid4()

    create_payload = SessionCreate(
        cohort_id=cohort_id,
        phase_id=phase_id,
        session_number=1,
        title="Session 1",
        start_at=datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc),
        sequence=1,
        tldv_meeting_id="meeting_abc123",
    )
    assert create_payload.tldv_meeting_id == "meeting_abc123"

    update_payload = SessionUpdate(
        tldv_meeting_id="meeting_xyz789",
        recording_status="ready",
        recording_duration_seconds=1800,
    )
    assert update_payload.tldv_meeting_id == "meeting_xyz789"
    assert update_payload.recording_status == "ready"
    assert update_payload.recording_duration_seconds == 1800


def test_discover_session_response_contract_preserved():
    """Verify Discover Session schemas maintain contract and backwards compatibility."""
    session_id = uuid4()
    detail = SessionDetailResponse(
        id=session_id,
        cohort_id=uuid4(),
        phase_id=uuid4(),
        session_number=1,
        session_type="learn_work",
        title="Orientation",
        status="completed",
        sequence=1,
        recording_url="https://external.example.com/rec",
        transcript_url="https://external.example.com/trans",
        has_recording=True,
        has_transcript=True,
        is_unlocked=True,
        tldv_meeting_id="tldv_999",
        recording_status="ready",
        recording_duration_seconds=3600,
    )
    assert detail.recording_url == "https://external.example.com/rec"
    assert detail.tldv_meeting_id == "tldv_999"
    assert detail.recording_status == "ready"


def test_session_db_model_persistence(db: Session):
    """Verify that tldv fields can be persisted and retrieved from database."""
    program = Program(name="Test Program", code=f"PROG_{uuid4().hex[:6]}")
    db.add(program)
    db.flush()

    cohort = Cohort(program_id=program.id, name="Test Cohort", code=f"COH_{uuid4().hex[:6]}")
    db.add(cohort)
    db.flush()

    phase = Phase(
        program_id=program.id,
        name="DISCOVER",
        code="DISCOVER",
        development_role="THINK",
        sequence=1,
    )
    db.add(phase)
    db.flush()

    session_record = DBSession(
        cohort_id=cohort.id,
        phase_id=phase.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Test Session with tl;dv",
        sequence=1,
        status=SessionStatus.SCHEDULED,
        recording_url="https://legacy.example.com/video",
        transcript_url="https://legacy.example.com/transcript",
        tldv_meeting_id="tldv_sample_id_123",
        recording_status="ready",
        recording_duration_seconds=2400,
        transcript_data=[
            {"speaker": "Lead", "text": "Hello world", "start_time": 0.0, "end_time": 4.0}
        ],
    )
    db.add(session_record)
    db.commit()
    db.refresh(session_record)

    # Assertions on retrieved record
    assert session_record.id is not None
    assert session_record.tldv_meeting_id == "tldv_sample_id_123"
    assert session_record.recording_status == "ready"
    assert session_record.recording_duration_seconds == 2400
    assert isinstance(session_record.transcript_data, list)
    assert session_record.transcript_data[0]["speaker"] == "Lead"

    # Verify existing recording_url and transcript_url remain intact alongside tldv fields
    assert session_record.recording_url == "https://legacy.example.com/video"
    assert session_record.transcript_url == "https://legacy.example.com/transcript"

    # Cleanup
    db.delete(session_record)
    db.delete(phase)
    db.delete(cohort)
    db.delete(program)
    db.commit()
