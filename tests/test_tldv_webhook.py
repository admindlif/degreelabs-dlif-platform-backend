"""
Unit and integration tests for the tl;dv webhook endpoint:
POST /api/v1/webhooks/tldv
"""

import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.session import RecordingProcessingStatus, Session as DBSession


@pytest.fixture
def webhook_client():
    return TestClient(app)


def _sign(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


def test_webhook_rejects_missing_secret(webhook_client, monkeypatch):
    """When TLDV_WEBHOOK_SECRET is not configured, webhook requests return 401 (fail-closed)."""
    monkeypatch.setattr(settings, "tldv_webhook_secret", None)
    payload = json.dumps({"event": "MeetingReady", "id": "meet-123"}).encode()
    resp = webhook_client.post(
        "/api/v1/webhooks/tldv",
        content=payload,
        headers={"Content-Type": "application/json", "X-TLDV-Signature": "sha256=invalid"},
    )
    assert resp.status_code == 401
    assert "Invalid or missing webhook signature" in resp.json()["detail"]


def test_webhook_rejects_missing_or_invalid_signature(webhook_client, monkeypatch):
    """Invalid or missing signature returns 401."""
    monkeypatch.setattr(settings, "tldv_webhook_secret", "test-secret-123")
    payload = json.dumps({"event": "MeetingReady", "id": "meet-123"}).encode()

    # Missing header
    resp = webhook_client.post(
        "/api/v1/webhooks/tldv",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401

    # Invalid signature
    resp2 = webhook_client.post(
        "/api/v1/webhooks/tldv",
        content=payload,
        headers={"Content-Type": "application/json", "X-TLDV-Signature": "bad-signature"},
    )
    assert resp2.status_code == 401


def test_webhook_unhandled_event_acknowledged(webhook_client, monkeypatch):
    """Unhandled events (or transcript events) return 200 acknowledged without action."""
    secret = "test-secret-xyz"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "TranscriptReady", "id": "meet-123"}).encode()
    sig = _sign(payload, secret)

    resp = webhook_client.post(
        "/api/v1/webhooks/tldv",
        content=payload,
        headers={"Content-Type": "application/json", "X-TLDV-Signature": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "acknowledged", "action": "none"}


def test_webhook_missing_meeting_id_acknowledged(webhook_client, monkeypatch):
    """MeetingReady event with missing meeting ID returns 200 acknowledged without action."""
    secret = "test-secret-xyz"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "MeetingReady"}).encode()
    sig = _sign(payload, secret)

    resp = webhook_client.post(
        "/api/v1/webhooks/tldv",
        content=payload,
        headers={"Content-Type": "application/json", "X-TLDV-Signature": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "acknowledged", "action": "none"}


def test_webhook_no_matching_session(webhook_client, monkeypatch):
    """MeetingReady event for an unknown meeting ID returns 200 with action=no_match."""
    secret = "test-secret-xyz"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "MeetingReady", "id": "unknown-meet-999"}).encode()
    sig = _sign(payload, secret)

    with patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=None):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=payload,
            headers={"Content-Type": "application/json", "X-TLDV-Signature": f"sha256={sig}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "acknowledged", "action": "no_match"}


def test_webhook_session_already_ready(webhook_client, monkeypatch):
    """If session is already READY, returns 200 with action=already_ready (idempotent)."""
    secret = "test-secret-xyz"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "MeetingReady", "id": "meet-already-done"}).encode()
    sig = _sign(payload, secret)

    fake_session = DBSession(
        id=uuid4(),
        session_number=1,
        title="Session 1",
        tldv_meeting_id="meet-already-done",
        recording_processing_status=RecordingProcessingStatus.READY,
    )

    with patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=fake_session):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=payload,
            headers={"Content-Type": "application/json", "X-TLDV-Signature": f"sha256={sig}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "acknowledged", "action": "already_ready"}


def test_webhook_queues_processing_successfully(webhook_client, monkeypatch):
    """When valid MeetingReady event matches a pending session, returns 200 accepted and spawns thread."""
    secret = "test-secret-xyz"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    session_id = uuid4()
    payload = json.dumps({"event": "MeetingReady", "id": "meet-new-recording"}).encode()
    sig = _sign(payload, secret)

    fake_session = DBSession(
        id=session_id,
        session_number=2,
        title="Session 2",
        tldv_meeting_id="meet-new-recording",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    with (
        patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=fake_session),
        patch("app.api.routes.webhooks._trigger_processing") as mock_trigger,
    ):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=payload,
            headers={"Content-Type": "application/json", "X-TLDV-Signature": f"sha256={sig}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "accepted", "action": "processing_queued"}


def test_webhook_auth_via_secret_header(webhook_client, monkeypatch):
    """tl;dv custom header X-Webhook-Secret or x-api-key provides valid authentication."""
    secret = "custom-header-secret"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "MeetingReady", "id": "meet-123"}).encode()
    with patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=None):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=payload,
            headers={"Content-Type": "application/json", "X-Webhook-Secret": secret},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"


def test_webhook_auth_via_bearer_token(webhook_client, monkeypatch):
    """Authorization: Bearer <secret> provides valid authentication."""
    secret = "bearer-token-secret"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    payload = json.dumps({"event": "MeetingReady", "id": "meet-123"}).encode()
    with patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=None):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"


def test_webhook_nested_data_meeting_ready(webhook_client, monkeypatch):
    """Official tl;dv MeetingReady nested payload data.id is correctly extracted."""
    secret = "test-secret"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    session_id = uuid4()
    nested_payload = json.dumps({
        "id": "webhook-job-123",
        "event": "MeetingReady",
        "data": {
            "id": "meet-nested-abc",
            "name": "DLIF Session",
        },
    }).encode()

    fake_session = DBSession(
        id=session_id,
        session_number=1,
        title="Session 1",
        tldv_meeting_id="meet-nested-abc",
        recording_processing_status=RecordingProcessingStatus.PENDING,
    )

    with (
        patch("app.api.routes.webhooks._find_session_by_meeting_id", return_value=fake_session) as mock_find,
        patch("app.api.routes.webhooks._trigger_processing"),
    ):
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=nested_payload,
            headers={"Content-Type": "application/json", "X-Webhook-Secret": secret},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "accepted", "action": "processing_queued"}
        mock_find.assert_called_once_with("meet-nested-abc")


def test_webhook_transcript_ready_persists_data(webhook_client, monkeypatch):
    """TranscriptReady saves transcript segments directly."""
    secret = "test-secret"
    monkeypatch.setattr(settings, "tldv_webhook_secret", secret)

    transcript_payload = json.dumps({
        "event": "TranscriptReady",
        "data": {
            "meetingId": "meet-transcript-123",
            "data": [
                {"startTime": 0, "endTime": 10, "speaker": "Instructor", "text": "Welcome to DLIF"},
            ],
        },
    }).encode()

    with patch("app.api.routes.webhooks._save_transcript_data", return_value=True) as mock_save:
        resp = webhook_client.post(
            "/api/v1/webhooks/tldv",
            content=transcript_payload,
            headers={"Content-Type": "application/json", "X-Webhook-Secret": secret},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "accepted", "action": "transcript_saved"}
        mock_save.assert_called_once()


