import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import admin as admin_routes
from app.core.auth_cookies import ONBOARDING_COOKIE_NAME
from app.core.config import Settings
from app.models.user import AccountStatus, User
from app.models.user_invitation import UserInvitationToken
from app.schemas.admin_crud import ResourceCreate, SessionCreate, SessionUpdate
from app.schemas.auth import CreateFellowRequest
from app.schemas.feedback import SubmissionFeedbackUpsertRequest
from app.schemas.submission import TeamSubmissionUpsertRequest
from app.services.auth import admin_create_fellow
from app.services.email import LoggingEmailBackend


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://database.invalid/dlif_test",
        "secret_key": "s" * 32,
        "frontend_base_url": "https://portal.example.com",
        "email_from_address": "noreply@example.com",
        "email_from_name": "DegreeLabs",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_cors_origins_are_environment_driven_and_production_safe():
    development = _settings()
    assert "http://localhost:3000" in development.allowed_origins
    assert "http://localhost:3002" in development.allowed_origins

    production = _settings(
        environment="production",
        email_backend="smtp",
        smtp_username="mailer",
        smtp_password="configured-at-runtime",
        cors_allowed_origins=(
            "https://fellows.example.com,https://admin.example.com"
        ),
    )
    assert production.allowed_origins == [
        "https://fellows.example.com",
        "https://admin.example.com",
    ]
    assert production.effective_auth_cookie_secure is True

    with pytest.raises(ValidationError):
        _settings(environment="production")

    with pytest.raises(ValidationError):
        _settings(
            environment="production",
            email_backend="smtp",
            smtp_username="mailer",
            smtp_password="configured-at-runtime",
            cors_allowed_origins="http://localhost:3000",
        )

    with pytest.raises(ValidationError):
        _settings(
            environment="production",
            email_backend="smtp",
            smtp_username="mailer",
            smtp_password="configured-at-runtime",
            auth_cookie_secure=False,
        )


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "javascript:alert(1)",
        "data:text/html,hello",
        "file:///tmp/private",
        "not a url",
        "https://user:password@example.com/private",
    ],
)
def test_external_url_inputs_reject_unsafe_schemes_and_malformed_values(
    unsafe_url: str,
):
    with pytest.raises(ValidationError):
        ResourceCreate(
            phase_id=uuid4(),
            title="Guide",
            url=unsafe_url,
        )

    with pytest.raises(ValidationError):
        SessionUpdate(recording_url=unsafe_url)

    with pytest.raises(ValidationError):
        SubmissionFeedbackUpsertRequest(
            feedback_text="Review",
            feedback_url=unsafe_url,
            status="accepted",
        )


def test_optional_urls_trim_and_convert_blank_to_none():
    resource = ResourceCreate(
        phase_id=uuid4(),
        title="Guide",
        url="   ",
    )
    session = SessionUpdate(
        meeting_url="  https://meet.example.com/room  ",
        recording_url=" ",
    )

    assert resource.url is None
    assert session.meeting_url == "https://meet.example.com/room"
    assert session.recording_url is None


def test_submission_url_is_limited_to_google_drive_hosts():
    assert TeamSubmissionUpsertRequest(
        drive_url=" https://drive.google.com/file/d/123/view "
    ).drive_url == "https://drive.google.com/file/d/123/view"
    assert TeamSubmissionUpsertRequest(
        drive_url="https://docs.google.com/document/d/123/edit"
    ).drive_url == "https://docs.google.com/document/d/123/edit"

    with pytest.raises(ValidationError):
        TeamSubmissionUpsertRequest(
            drive_url="https://drive.google.com.evil.example/file"
        )

    with pytest.raises(ValidationError):
        TeamSubmissionUpsertRequest(drive_url="   ")


def test_session_create_rejects_unsafe_meeting_url():
    with pytest.raises(ValidationError):
        SessionCreate(
            cohort_id=uuid4(),
            phase_id=uuid4(),
            session_number=1,
            title="Session",
            meeting_url="javascript:alert(1)",
            sequence=1,
        )


def test_invalid_resource_url_returns_422(
    client: TestClient,
    admin_headers: dict[str, str],
):
    response = client.post(
        "/api/v1/admin/resources",
        headers=admin_headers,
        json={
            "phase_id": str(uuid4()),
            "title": "Unsafe resource",
            "url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 422


def test_invited_fellow_can_resume_onboarding_but_active_account_cannot(
    client: TestClient,
    db: Session,
):
    email = f"resume_{uuid4().hex[:8]}@example.com"
    password = "ResumePass123!"
    user, raw_token = admin_create_fellow(
        db,
        CreateFellowRequest(
            first_name="Resume",
            last_name="Fellow",
            email=email,
        ),
    )

    try:
        activation = client.post(
            "/api/v1/auth/activate",
            json={
                "token": raw_token,
                "password": password,
                "confirm_password": password,
            },
        )
        assert activation.status_code == 200

        resumed = client.post(
            "/api/v1/auth/onboarding/resume",
            json={"email": email, "password": password},
        )
        assert resumed.status_code == 200
        assert "onboarding_token" not in resumed.json()
        assert "access_token" not in resumed.json()
        assert client.cookies.get(ONBOARDING_COOKIE_NAME) is not None

        unknown = client.post(
            "/api/v1/auth/onboarding/resume",
            json={"email": "unknown@example.com", "password": password},
        )
        wrong_password = client.post(
            "/api/v1/auth/onboarding/resume",
            json={"email": email, "password": "wrong-password"},
        )
        assert unknown.status_code == wrong_password.status_code == 401
        assert unknown.json()["detail"] == wrong_password.json()["detail"]

        user.account_status = AccountStatus.ACTIVE
        user.two_factor_enabled = True
        db.commit()
        completed = client.post(
            "/api/v1/auth/onboarding/resume",
            json={"email": email, "password": password},
        )
        assert completed.status_code == 401
        assert completed.json()["detail"] == unknown.json()["detail"]
    finally:
        db.delete(user)
        db.commit()


def test_invitation_delivery_failure_is_reported_and_user_persists(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    email = f"delivery_{uuid4().hex[:8]}@example.com"

    def fail_delivery(**_kwargs: object) -> None:
        raise RuntimeError("private SMTP diagnostic")

    monkeypatch.setattr(admin_routes, "send_invitation_email", fail_delivery)
    response = client.post(
        "/api/v1/admin/fellows",
        headers=admin_headers,
        json={
            "first_name": "Delivery",
            "last_name": "Failure",
            "email": email,
        },
    )

    assert response.status_code == 201
    assert response.json()["invitation_sent"] is False
    assert "private SMTP diagnostic" not in response.json()["message"]

    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    assert user.account_status == AccountStatus.INVITED

    db.delete(user)
    db.commit()


def test_resend_invitation_reuses_user_and_replaces_outstanding_link(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    email = f"resend_{uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(
        admin_routes,
        "send_invitation_email",
        lambda **_kwargs: None,
    )
    created = client.post(
        "/api/v1/admin/fellows",
        headers=admin_headers,
        json={
            "first_name": "Resend",
            "last_name": "Fellow",
            "email": email,
        },
    )
    assert created.status_code == 201
    fellow_id = created.json()["id"]

    resent = client.post(
        f"/api/v1/admin/fellows/{fellow_id}/resend-invitation",
        headers=admin_headers,
    )
    assert resent.status_code == 200
    assert resent.json()["invitation_sent"] is True

    users = db.scalars(select(User).where(User.email == email)).all()
    assert len(users) == 1
    invitations = db.scalars(
        select(UserInvitationToken).where(
            UserInvitationToken.user_id == users[0].id
        )
    ).all()
    assert len(invitations) == 2
    assert len([item for item in invitations if item.used_at is None]) == 1

    db.delete(users[0])
    db.commit()


def test_logging_email_backend_omits_recipient_and_body(caplog: pytest.LogCaptureFixture):
    recipient = "private-recipient@example.com"
    body = "https://portal.example.com/activate?token=private-value"

    with caplog.at_level(logging.INFO, logger="app.services.email"):
        LoggingEmailBackend().send(
            to_address=recipient,
            subject="Invitation",
            html_body=body,
            text_body=body,
        )

    output = caplog.text
    assert recipient not in output
    assert body not in output
    assert "private-value" not in output
