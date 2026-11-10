"""
Tests for Phase 2 tl;dv Integration in DLIF Fellow Portal:
- Authorized fellow access
- Cross-cohort access denial (404)
- Locked session denial (403)
- Unauthenticated requests (401)
- Mock recording playback
- Mock transcript retrieval
- Missing API key behavior
- Existing cached transcripts
- Legacy recording and transcript URL compatibility
- HttpTLDVClient unit tests (redirects, error handling, timeouts)
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.models.cohort import Cohort
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.phase import Phase
from app.models.program import Program
from app.models.session import Session as DBSession, SessionStatus, SessionType
from app.models.user import AccountStatus, User, UserRole
from app.services.tldv import (
    HttpTLDVClient,
    MockTLDVClient,
    get_tldv_client,
)


@pytest.fixture(autouse=True)
def isolate_r2_playback_lookup(monkeypatch):
    """Keep media API tests isolated from configured external R2 storage."""
    monkeypatch.setattr("app.services.tldv.object_exists", lambda _key: False)


@pytest.fixture
def test_setup(db: Session):
    """
    Sets up a full multi-cohort test environment with:
    - Program & Phase
    - Cohort A & Cohort B
    - Fellow A enrolled in Cohort A
    - Fellow B enrolled in Cohort B
    - Sessions in Cohort A:
        * session_unlocked_tldv (unlocked, with tldv_meeting_id)
        * session_locked_tldv (locked, with tldv_meeting_id)
        * session_cached_transcript (unlocked, with tldv_meeting_id & cached transcript_data)
        * session_legacy_only (unlocked, no tldv_meeting_id, has legacy recording_url & transcript_url)
    - Session in Cohort B:
        * session_cohort_b (unlocked, in Cohort B)
    """
    program = Program(name="DegreeLabs Impact Fellowship", code=f"DLIF_{uuid4().hex[:6]}")
    db.add(program)
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

    cohort_a = Cohort(program_id=program.id, name="Cohort A", code=f"COHA_{uuid4().hex[:6]}")
    cohort_b = Cohort(program_id=program.id, name="Cohort B", code=f"COHB_{uuid4().hex[:6]}")
    db.add_all([cohort_a, cohort_b])
    db.flush()

    fellow_a = User(
        first_name="Alice",
        last_name="Fellow",
        email=f"alice_{uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    fellow_b = User(
        first_name="Bob",
        last_name="Fellow",
        email=f"bob_{uuid4().hex[:6]}@example.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([fellow_a, fellow_b])
    db.flush()

    enrollment_a = Enrollment(
        user_id=fellow_a.id,
        cohort_id=cohort_a.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    enrollment_b = Enrollment(
        user_id=fellow_b.id,
        cohort_id=cohort_b.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    db.add_all([enrollment_a, enrollment_b])
    db.flush()

    # Cohort A Sessions
    session_unlocked_tldv = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Session 1 Unlocked tl;dv",
        sequence=1,
        status=SessionStatus.COMPLETED,
        is_unlocked=True,
        tldv_meeting_id="meeting_a1",
        recording_status="ready",
        recording_duration_seconds=1800,
    )
    session_locked_tldv = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=2,
        session_type=SessionType.LEARN_WORK,
        title="Session 2 Locked",
        sequence=2,
        status=SessionStatus.SCHEDULED,
        is_unlocked=False,
        tldv_meeting_id="meeting_a2",
    )
    session_cached_transcript = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=3,
        session_type=SessionType.LEARN_WORK,
        title="Session 3 Cached Transcript",
        sequence=3,
        status=SessionStatus.COMPLETED,
        is_unlocked=True,
        tldv_meeting_id="meeting_a3",
        transcript_data=[
            {
                "speaker": "Mentor Marcus",
                "text": "Hello team, let us review the research hypothesis.",
                "start_time": 0.0,
                "end_time": 10.5,
            },
            {
                "speaker": "Fellow Alice",
                "text": "We analyzed the primary market drivers.",
                "start_time": 11.0,
                "end_time": 25.0,
            },
        ],
    )
    session_legacy_only = DBSession(
        cohort_id=cohort_a.id,
        phase_id=phase.id,
        session_number=4,
        session_type=SessionType.LEARN_WORK,
        title="Session 4 Legacy URLs",
        sequence=4,
        status=SessionStatus.COMPLETED,
        is_unlocked=True,
        tldv_meeting_id=None,
        recording_url="https://legacy-storage.example.com/recordings/sess4.mp4",
        transcript_url="https://legacy-storage.example.com/transcripts/sess4.vtt",
    )

    # Cohort B Session
    session_cohort_b = DBSession(
        cohort_id=cohort_b.id,
        phase_id=phase.id,
        session_number=1,
        session_type=SessionType.LEARN_WORK,
        title="Cohort B Session",
        sequence=1,
        status=SessionStatus.COMPLETED,
        is_unlocked=True,
        tldv_meeting_id="meeting_b1",
    )

    db.add_all([
        session_unlocked_tldv,
        session_locked_tldv,
        session_cached_transcript,
        session_legacy_only,
        session_cohort_b,
    ])
    db.commit()

    token_a = create_access_token(str(fellow_a.id), role=fellow_a.role.value)
    token_b = create_access_token(str(fellow_b.id), role=fellow_b.role.value)

    yield {
        "fellow_a": fellow_a,
        "fellow_b": fellow_b,
        "token_a": token_a,
        "token_b": token_b,
        "headers_a": {"Authorization": f"Bearer {token_a}"},
        "headers_b": {"Authorization": f"Bearer {token_b}"},
        "session_unlocked_tldv": session_unlocked_tldv,
        "session_locked_tldv": session_locked_tldv,
        "session_cached_transcript": session_cached_transcript,
        "session_legacy_only": session_legacy_only,
        "session_cohort_b": session_cohort_b,
    }

    # Teardown
    db.query(DBSession).filter(
        DBSession.id.in_([
            session_unlocked_tldv.id,
            session_locked_tldv.id,
            session_cached_transcript.id,
            session_legacy_only.id,
            session_cohort_b.id,
        ])
    ).delete(synchronize_session=False)
    db.query(Enrollment).filter(
        Enrollment.id.in_([enrollment_a.id, enrollment_b.id])
    ).delete(synchronize_session=False)
    db.query(Cohort).filter(Cohort.id.in_([cohort_a.id, cohort_b.id])).delete(synchronize_session=False)
    db.delete(phase)
    db.delete(program)
    db.query(User).filter(User.id.in_([fellow_a.id, fellow_b.id])).delete(synchronize_session=False)
    db.commit()


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_unauthenticated_requests_rejected(client: TestClient, test_setup):
    """Unauthenticated requests must be rejected with 401."""
    session_id = test_setup["session_unlocked_tldv"].id

    resp_rec = client.get(f"/api/v1/fellow/sessions/{session_id}/recording")
    assert resp_rec.status_code == 401

    resp_trans = client.get(f"/api/v1/fellow/sessions/{session_id}/transcript")
    assert resp_trans.status_code == 401


def test_cross_cohort_session_access_denied(client: TestClient, test_setup):
    """
    Fellow A attempting to access Cohort B's session must return 404,
    preventing unauthorized cohort discovery.
    """
    headers = test_setup["headers_a"]
    session_b_id = test_setup["session_cohort_b"].id

    resp_rec = client.get(f"/api/v1/fellow/sessions/{session_b_id}/recording", headers=headers)
    assert resp_rec.status_code == 404
    assert resp_rec.json()["detail"] == "Session not found."

    resp_trans = client.get(f"/api/v1/fellow/sessions/{session_b_id}/transcript", headers=headers)
    assert resp_trans.status_code == 404
    assert resp_trans.json()["detail"] == "Session not found."


def test_locked_session_access_denied(client: TestClient, test_setup):
    """
    Fellow attempting to access a locked session in their cohort must receive 403 Forbidden.
    """
    headers = test_setup["headers_a"]
    locked_session_id = test_setup["session_locked_tldv"].id

    resp_rec = client.get(f"/api/v1/fellow/sessions/{locked_session_id}/recording", headers=headers)
    assert resp_rec.status_code == 403
    assert "locked" in resp_rec.json()["detail"].lower()

    resp_trans = client.get(f"/api/v1/fellow/sessions/{locked_session_id}/transcript", headers=headers)
    assert resp_trans.status_code == 403
    assert "locked" in resp_trans.json()["detail"].lower()


def test_mock_recording_playback_success(client: TestClient, test_setup, monkeypatch):
    """
    When mock mode is explicitly enabled, return ready status with test video URL
    without redirecting to tl;dv dashboard.
    """
    monkeypatch.setattr(settings, "tldv_mock_mode", True)
    monkeypatch.setattr(settings, "tldv_api_key", None)

    headers = test_setup["headers_a"]
    session_id = test_setup["session_unlocked_tldv"].id

    response = client.get(f"/api/v1/fellow/sessions/{session_id}/recording", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["has_recording"] is True
    assert data["status"] == "ready"
    assert data["provider"] == "tldv"
    assert data["playback_url"] is not None
    assert "http" in data["playback_url"]
    assert data["duration_seconds"] == 596
    assert data["expires_at"] is not None
    # Crucial security assertion: NEVER expose API key or redirect
    assert "api_key" not in data
    assert "pasta.tldv.io" not in data["playback_url"]


def test_mock_transcript_retrieval_success(client: TestClient, test_setup, monkeypatch):
    """
    When mock mode is explicitly enabled, return structured transcript segments.
    """
    monkeypatch.setattr(settings, "tldv_mock_mode", True)
    monkeypatch.setattr(settings, "tldv_api_key", None)

    headers = test_setup["headers_a"]
    session_id = test_setup["session_unlocked_tldv"].id

    response = client.get(f"/api/v1/fellow/sessions/{session_id}/transcript", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["has_transcript"] is True
    assert data["status"] == "ready"
    assert data["provider"] == "tldv"
    assert len(data["segments"]) == 3

    first_segment = data["segments"][0]
    assert first_segment["speaker"] == "Programme Lead"
    assert "DLIF fellowship" in first_segment["text"]
    assert first_segment["start_time"] == 0.0
    assert first_segment["end_time"] == 14.5
    assert "DLIF fellowship" in data["full_text"]


def test_missing_api_key_and_mock_disabled(client: TestClient, test_setup, monkeypatch):
    """
    When no API key is configured and mock mode is disabled (default production posture),
    return appropriate unavailable status and do not crash or default to mock.
    """
    monkeypatch.setattr(settings, "tldv_mock_mode", False)
    monkeypatch.setattr(settings, "tldv_api_key", None)

    headers = test_setup["headers_a"]
    session_id = test_setup["session_unlocked_tldv"].id

    # Client factory should yield None
    assert get_tldv_client() is None

    resp_rec = client.get(f"/api/v1/fellow/sessions/{session_id}/recording", headers=headers)
    assert resp_rec.status_code == 200
    rec_data = resp_rec.json()
    assert rec_data["has_recording"] is False
    assert rec_data["playback_url"] is None

    resp_trans = client.get(f"/api/v1/fellow/sessions/{session_id}/transcript", headers=headers)
    assert resp_trans.status_code == 200
    trans_data = resp_trans.json()
    assert trans_data["has_transcript"] is False
    assert trans_data["segments"] == []


def test_cached_transcript_data_returned(client: TestClient, test_setup, monkeypatch):
    """
    When cached transcript_data exists on the session model, it must be returned
    directly without requiring an external client call.
    """
    monkeypatch.setattr(settings, "tldv_mock_mode", False)
    monkeypatch.setattr(settings, "tldv_api_key", None)

    headers = test_setup["headers_a"]
    session_id = test_setup["session_cached_transcript"].id

    response = client.get(f"/api/v1/fellow/sessions/{session_id}/transcript", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["has_transcript"] is True
    assert data["status"] == "ready"
    assert len(data["segments"]) == 2
    assert data["segments"][0]["speaker"] == "Mentor Marcus"
    assert data["segments"][1]["speaker"] == "Fellow Alice"
    assert "research hypothesis" in data["segments"][0]["text"]
    assert "primary market drivers" in data["full_text"]


def test_legacy_recording_and_transcript_compatibility(client: TestClient, test_setup, monkeypatch):
    """
    When a session has legacy recording_url and transcript_url and no tldv_meeting_id,
    the endpoints gracefully fall back to the legacy URLs.
    """
    monkeypatch.setattr(settings, "tldv_mock_mode", False)
    monkeypatch.setattr(settings, "tldv_api_key", None)

    headers = test_setup["headers_a"]
    session_id = test_setup["session_legacy_only"].id

    resp_rec = client.get(f"/api/v1/fellow/sessions/{session_id}/recording", headers=headers)
    assert resp_rec.status_code == 200
    rec_data = resp_rec.json()
    assert rec_data["has_recording"] is True
    assert rec_data["status"] == "ready"
    assert rec_data["provider"] == "legacy"
    assert rec_data["playback_url"] == "https://legacy-storage.example.com/recordings/sess4.mp4"

    resp_trans = client.get(f"/api/v1/fellow/sessions/{session_id}/transcript", headers=headers)
    assert resp_trans.status_code == 200
    trans_data = resp_trans.json()
    assert trans_data["has_transcript"] is True
    assert trans_data["status"] == "ready"
    assert trans_data["provider"] == "legacy"
    assert "https://legacy-storage.example.com/transcripts/sess4.vtt" in trans_data["full_text"]


# ---------------------------------------------------------------------------
# HttpTLDVClient Unit Tests
# ---------------------------------------------------------------------------


_real_httpx_client = httpx.Client


def test_http_tldv_client_download_redirect(monkeypatch):
    """
    Test HttpTLDVClient correctly handles tl;dv download 302 redirect
    yielding signed playback URL.
    """
    client = HttpTLDVClient(api_key="test_api_key_123", base_url="https://pasta.tldv.io")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "test_api_key_123"
        assert request.url.path == "/v1alpha1/meetings/meet_123/download"
        return httpx.Response(
            status_code=302,
            headers={"Location": "https://signed.s3.amazonaws.com/meet_123.mp4?signature=xyz"},
        )

    transport = httpx.MockTransport(mock_handler)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    playback = client.get_recording_playback("meet_123")
    assert playback.status == "ready"
    assert playback.playback_url == "https://signed.s3.amazonaws.com/meet_123.mp4?signature=xyz"
    assert playback.expires_at is not None


def test_http_tldv_client_transcript_parsing(monkeypatch):
    """
    Test HttpTLDVClient correctly parses tl;dv JSON transcript segments.
    """
    client = HttpTLDVClient(api_key="test_api_key_123", base_url="https://pasta.tldv.io")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "test_api_key_123"
        assert request.url.path == "/v1alpha1/meetings/meet_456/transcript"
        return httpx.Response(
            status_code=200,
            json={
                "segments": [
                    {
                        "speaker": "Speaker 1",
                        "text": "First point.",
                        "startTime": 0.5,
                        "endTime": 3.0,
                    },
                    {
                        "speaker": "Speaker 2",
                        "text": "Second point.",
                        "startTime": 3.5,
                        "endTime": 6.0,
                    },
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    transcript = client.get_transcript("meet_456")
    assert transcript.status == "ready"
    assert len(transcript.segments) == 2
    assert transcript.segments[0].speaker == "Speaker 1"
    assert transcript.segments[0].start_time == 0.5
    assert transcript.segments[0].end_time == 3.0
    assert transcript.full_text == "First point. Second point."

    # Also test official tl;dv response envelope using "data" key
    def mock_data_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "id": "trans_1",
                "meetingId": "meet_789",
                "data": [
                    {
                        "speaker": "Speaker A",
                        "text": "Official envelope text.",
                        "startTime": 1.0,
                        "endTime": 4.0,
                    }
                ],
            },
        )

    transport_data = httpx.MockTransport(mock_data_handler)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport_data, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    transcript_data_env = client.get_transcript("meet_789")
    assert transcript_data_env.status == "ready"
    assert len(transcript_data_env.segments) == 1
    assert transcript_data_env.segments[0].speaker == "Speaker A"
    assert transcript_data_env.segments[0].text == "Official envelope text."


def test_http_tldv_client_error_handling(monkeypatch):
    """
    Test HttpTLDVClient handles 404, 422, and network errors gracefully.
    """
    client = HttpTLDVClient(api_key="test_api_key_123")

    # 404 Not Found
    transport_404 = httpx.MockTransport(lambda req: httpx.Response(status_code=404))
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport_404, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    assert client.get_recording_playback("unknown").status == "not_available"
    assert client.get_transcript("unknown").status == "not_available"

    # 422 Processing
    transport_422 = httpx.MockTransport(lambda req: httpx.Response(status_code=422))
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport_422, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    assert client.get_recording_playback("processing_id").status == "processing"
    assert client.get_transcript("processing_id").status == "processing"

    # RequestError / Network timeout
    def raise_network_err(req):
        raise httpx.ConnectTimeout("Connection timed out")

    transport_err = httpx.MockTransport(raise_network_err)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: _real_httpx_client(*a, transport=transport_err, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    assert client.get_recording_playback("timeout_id").status == "not_available"
    assert client.get_transcript("timeout_id").status == "not_available"


def test_poc_seekable_streaming_endpoint(test_setup, monkeypatch):
    """
    Test development-only POC seekable streaming endpoint:
    - Auth & Permissions (401, 403, 404)
    - Full content delivery (200 OK)
    - Byte-range delivery (206 Partial Content)
    - Out of range (416)
    """
    import tempfile
    from pathlib import Path
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    auth_header = {"Authorization": f"Bearer {test_setup['token_a']}"}

    # 1. Unauthenticated request rejected
    r_unauth = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/poc-seekable-stream"
    )
    assert r_unauth.status_code == 401

    # 2. Locked session access rejected
    r_locked = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_locked_tldv'].id}/poc-seekable-stream",
        headers=auth_header,
    )
    assert r_locked.status_code == 403

    # 3. Cross-cohort access rejected
    r_cross = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_cohort_b'].id}/poc-seekable-stream",
        headers=auth_header,
    )
    assert r_cross.status_code == 404

    # 4. No POC file available -> 404
    r_nofile = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/poc-seekable-stream",
        headers=auth_header,
    )
    assert r_nofile.status_code == 404

    # 5. Create temporary seekable file and point candidate path to it
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        dummy_poc_dir = tmp_path / "tmp" / "poc_media"
        dummy_poc_dir.mkdir(parents=True)
        dummy_file = dummy_poc_dir / (
            f"session_{test_setup['session_unlocked_tldv'].id}_seekable.mp4"
        )
        dummy_content = b"0123456789" * 100  # 1000 bytes
        dummy_file.write_bytes(dummy_content)

        monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))

        # Test full 200 delivery
        r_full = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/poc-seekable-stream",
            headers=auth_header,
        )
        assert r_full.status_code == 200
        assert r_full.headers["Accept-Ranges"] == "bytes"
        assert r_full.headers["Content-Length"] == "1000"
        assert r_full.headers["Content-Type"] == "video/mp4"
        assert r_full.content == dummy_content

        # Test range 206 delivery
        r_range = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/poc-seekable-stream",
            headers={**auth_header, "Range": "bytes=10-19"},
        )
        assert r_range.status_code == 206
        assert r_range.headers["Content-Range"] == "bytes 10-19/1000"
        assert r_range.headers["Content-Length"] == "10"
        assert r_range.content == dummy_content[10:20]

        # Test out-of-range 416
        r_oor = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/poc-seekable-stream",
            headers={**auth_header, "Range": "bytes=2000-3000"},
        )
        assert r_oor.status_code == 416
        assert r_oor.headers["Content-Range"] == "bytes */1000"




def test_generic_seekable_streaming_endpoint(test_setup, monkeypatch):
    """
    Test generic seekable-stream endpoint:
    - Auth & Permissions (401, 403, 404)
    - Full content delivery (200 OK)
    - Byte-range delivery (206 Partial Content)
    - Out of range (416)
    """
    import tempfile
    from pathlib import Path
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    auth_header = {"Authorization": f"Bearer {test_setup['token_a']}"}

    # 1. Unauthenticated request rejected
    r_unauth = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/seekable-stream"
    )
    assert r_unauth.status_code == 401

    # 2. Locked session access rejected
    r_locked = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_locked_tldv'].id}/seekable-stream",
        headers=auth_header,
    )
    assert r_locked.status_code == 403

    # 3. Cross-cohort access rejected
    r_cross = client.get(
        f"/api/v1/fellow/sessions/{test_setup['session_cohort_b'].id}/seekable-stream",
        headers=auth_header,
    )
    assert r_cross.status_code == 404

    # 4. Create temporary seekable file and test delivery
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        dummy_poc_dir = tmp_path / "tmp" / "poc_media"
        dummy_poc_dir.mkdir(parents=True)
        dummy_file = dummy_poc_dir / f"session_{test_setup['session_unlocked_tldv'].id}_seekable.mp4"
        dummy_content = b"abcdefghij" * 100  # 1000 bytes
        dummy_file.write_bytes(dummy_content)

        monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))

        # Test full 200 delivery
        r_full = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/seekable-stream",
            headers=auth_header,
        )
        assert r_full.status_code == 200
        assert r_full.headers["Accept-Ranges"] == "bytes"
        assert r_full.headers["Content-Length"] == "1000"
        assert r_full.headers["Content-Type"] == "video/mp4"
        assert r_full.content == dummy_content

        # Test range 206 delivery
        r_range = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/seekable-stream",
            headers={**auth_header, "Range": "bytes=0-9"},
        )
        assert r_range.status_code == 206
        assert r_range.headers["Content-Range"] == "bytes 0-9/1000"
        assert r_range.headers["Content-Length"] == "10"
        assert r_range.content == b"abcdefghij"

        # Test out-of-range 416
        r_oor = client.get(
            f"/api/v1/fellow/sessions/{test_setup['session_unlocked_tldv'].id}/seekable-stream",
            headers={**auth_header, "Range": "bytes=5000-6000"},
        )
        assert r_oor.status_code == 416
        assert r_oor.headers["Content-Range"] == "bytes */1000"
