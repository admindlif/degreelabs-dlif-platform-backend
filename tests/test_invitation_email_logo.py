import pytest
from uuid import uuid4

from app.core.config import settings
from app.models.user import AccountStatus, User, UserRole
from app.services.invitation import send_invitation_email


def test_invitation_email_contains_official_logo(monkeypatch: pytest.MonkeyPatch):
    sent_emails = []

    def mock_send_email(*, to_address: str, subject: str, html_body: str, text_body: str) -> None:
        sent_emails.append({
            "to_address": to_address,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
        })

    monkeypatch.setattr("app.services.invitation.send_email", mock_send_email)

    user = User(
        id=uuid4(),
        email="test_fellow@example.com",
        first_name="Jane",
        last_name="Doe",
        role=UserRole.STUDENT,
        account_status=AccountStatus.INVITED,
    )

    send_invitation_email(user=user, raw_token="test-token-1234")

    assert len(sent_emails) == 1
    email = sent_emails[0]
    html = email["html_body"]
    assert email["subject"] == "You've been invited to DegreeLabs DLIF as a Fellow"

    # Logo element
    assert "<img" in html
    assert 'alt="DegreeLabs"' in html
    assert "https://framerusercontent.com/images/wQtZcQz0JelTmeZro2xPdEUWPwI.png" in html

    # White header styling without navy background or badge
    assert 'style="background:#ffffff;padding:32px 40px 24px;border-bottom:1px solid #f3f4f6;"' in html
    assert 'style="background:#0f172a;padding:28px 40px;"' not in html
    assert 'border-radius:6px;padding:8px 16px;' not in html

    # Activate Account button preserved
    assert "background:#2563eb;" in html
    assert "Activate Account" in html


def test_invitation_email_uses_configured_logo_url(monkeypatch: pytest.MonkeyPatch):
    sent_emails = []

    def mock_send_email(*, to_address: str, subject: str, html_body: str, text_body: str) -> None:
        sent_emails.append({
            "to_address": to_address,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
        })

    monkeypatch.setattr("app.services.invitation.send_email", mock_send_email)
    monkeypatch.setattr(settings, "email_logo_url", "https://cdn.degreelabs.com/assets/logo.png")

    user = User(
        id=uuid4(),
        email="test_fellow2@example.com",
        first_name="John",
        last_name="Smith",
        role=UserRole.STUDENT,
        account_status=AccountStatus.INVITED,
    )

    send_invitation_email(user=user, raw_token="test-token-5678")

    assert len(sent_emails) == 1
    assert "https://cdn.degreelabs.com/assets/logo.png" in sent_emails[0]["html_body"]


def test_invitation_email_uses_https_frontend_base_url(monkeypatch: pytest.MonkeyPatch):
    sent_emails = []

    def mock_send_email(*, to_address: str, subject: str, html_body: str, text_body: str) -> None:
        sent_emails.append({
            "to_address": to_address,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
        })

    monkeypatch.setattr("app.services.invitation.send_email", mock_send_email)
    monkeypatch.setattr(settings, "email_logo_url", None)
    monkeypatch.setattr(settings, "frontend_base_url", "https://fellow.degreelabs.com")

    user = User(
        id=uuid4(),
        email="test_fellow3@example.com",
        first_name="Alice",
        last_name="Wonder",
        role=UserRole.STUDENT,
        account_status=AccountStatus.INVITED,
    )

    send_invitation_email(user=user, raw_token="test-token-9999")

    assert len(sent_emails) == 1
    assert "https://fellow.degreelabs.com/degreelabs-logo.png" in sent_emails[0]["html_body"]
