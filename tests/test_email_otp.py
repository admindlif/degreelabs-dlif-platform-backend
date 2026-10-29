from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth_cookies import (
    ACCESS_COOKIE_NAME,
    ONBOARDING_COOKIE_NAME,
    TWO_FA_CHALLENGE_COOKIE_NAME,
)
from app.core.config import settings
from app.core.security import (
    create_2fa_challenge_token,
    create_onboarding_token,
    generate_totp_secret,
    hash_invitation_token,
    hash_password,
)
from app.models.email_otp import EmailOTPChallenge
from app.models.user import AccountStatus, User, UserRole
from app.models.user_invitation import UserInvitationToken
from app.services.email_otp import (
    EmailOTPCooldownError,
    EmailOTPForbiddenError,
    EmailOTPRateLimitError,
    generate_otp_code,
    hash_otp_code,
    mask_email,
    send_email_otp,
    verify_email_otp,
    verify_otp_code,
)


@pytest.fixture(autouse=True)
def use_logging_email(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "logging")


@pytest.fixture
def fellow_user(db: Session) -> User:
    user = User(
        first_name="Alice",
        last_name="Fellow",
        email=f"alice_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=True,
        totp_secret=generate_totp_secret(),
        preferred_auth_method="authenticator",
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def mentor_user(db: Session) -> User:
    user = User(
        first_name="Bob",
        last_name="Mentor",
        email=f"bob_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        role=UserRole.MENTOR,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=True,
        preferred_auth_method="email_otp",
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_user(db: Session) -> User:
    user = User(
        first_name="Charlie",
        last_name="Admin",
        email=f"admin_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password123!"),
        role=UserRole.ADMIN,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=True,
        totp_secret=generate_totp_secret(),
        preferred_auth_method="authenticator",
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Unit tests: Utilities
# ---------------------------------------------------------------------------


def test_mask_email():
    assert mask_email("fellow.e2e@example.com") == "f***@example.com"
    assert mask_email("a@degreelabs.org") == "a***@degreelabs.org"
    assert mask_email("") == "***"
    assert mask_email("invalid") == "***"


def test_generate_otp_code():
    code = generate_otp_code()
    assert len(code) == 6
    assert code.isdigit()


def test_hash_and_verify_otp_code():
    uid = uuid4()
    purpose = "login"
    code = "123456"

    code_hash = hash_otp_code(uid, purpose, code)
    assert len(code_hash) == 64
    assert verify_otp_code(uid, purpose, code, code_hash) is True
    assert verify_otp_code(uid, purpose, "654321", code_hash) is False
    assert verify_otp_code(uid, "activation", code, code_hash) is False
    assert verify_otp_code(uuid4(), purpose, code, code_hash) is False


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


def test_send_email_otp_privileged_rejected(db: Session, admin_user: User):
    with pytest.raises(EmailOTPForbiddenError):
        send_email_otp(db, admin_user, purpose="login")


def test_send_email_otp_success_and_cooldown(db: Session, fellow_user: User):
    challenge, code = send_email_otp(db, fellow_user, purpose="login")
    assert challenge is not None
    assert challenge.user_id == fellow_user.id
    assert challenge.purpose == "login"
    assert challenge.attempt_count == 0
    assert challenge.consumed_at is None

    # Immediate second request should trigger cooldown error
    with pytest.raises(EmailOTPCooldownError):
        send_email_otp(db, fellow_user, purpose="login")


def test_verify_email_otp_cycle(db: Session, fellow_user: User):
    challenge, code = send_email_otp(db, fellow_user, purpose="login")

    # Wrong code increments attempts
    with pytest.raises(Exception):
        verify_email_otp(db, fellow_user, purpose="login", code="000000")

    db.refresh(challenge)
    assert challenge.attempt_count == 1
    assert challenge.consumed_at is None

    # Correct code verifies and consumes
    verified_challenge = verify_email_otp(db, fellow_user, purpose="login", code=code)
    assert verified_challenge.consumed_at is not None

    # Consumed code cannot be reused
    with pytest.raises(Exception):
        verify_email_otp(db, fellow_user, purpose="login", code=code)


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def test_login_available_methods_fellow(client: TestClient, fellow_user: User):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": fellow_user.email, "password": "Password123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["requires_2fa"] is True
    assert "authenticator" in data["available_methods"]
    assert "email_otp" in data["available_methods"]
    assert data["default_method"] == "authenticator"
    assert data["masked_email"] is not None
    assert TWO_FA_CHALLENGE_COOKIE_NAME in response.cookies


def test_login_available_methods_mentor_email_otp_preferred(client: TestClient, mentor_user: User):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": mentor_user.email, "password": "Password123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["requires_2fa"] is True
    assert data["available_methods"] == ["email_otp"]
    assert data["default_method"] == "email_otp"


def test_login_admin_strictly_authenticator(client: TestClient, admin_user: User):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "Password123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["requires_2fa"] is True
    assert data["available_methods"] == ["authenticator"]
    assert data["default_method"] == "authenticator"

    # Attempting to send Email OTP for admin must return 403
    challenge_token = response.cookies[TWO_FA_CHALLENGE_COOKIE_NAME]
    client.cookies.set(TWO_FA_CHALLENGE_COOKIE_NAME, challenge_token)
    otp_resp = client.post(
        "/api/v1/auth/email-otp/send",
        json={"purpose": "login"},
    )
    assert otp_resp.status_code == 403


def test_email_otp_login_flow(client: TestClient, db: Session, fellow_user: User):
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": fellow_user.email, "password": "Password123!"},
    )
    assert login_resp.status_code == 200
    challenge_token = login_resp.cookies[TWO_FA_CHALLENGE_COOKIE_NAME]

    # Request Email OTP
    client.cookies.set(TWO_FA_CHALLENGE_COOKIE_NAME, challenge_token)
    send_resp = client.post(
        "/api/v1/auth/email-otp/send",
        json={"purpose": "login"},
    )
    assert send_resp.status_code == 200
    send_data = send_resp.json()
    assert "Verification code sent" in send_data["message"]
    assert send_data["masked_email"] == mask_email(fellow_user.email)

    # Fetch OTP from db
    latest_challenge = db.query(EmailOTPChallenge).filter_by(user_id=fellow_user.id, purpose="login").first()
    assert latest_challenge is not None

    # Compute expected code by verifying against possible values or helper
    code = None
    for candidate in range(1_000_000):
        c_str = f"{candidate:06d}"
        if verify_otp_code(fellow_user.id, "login", c_str, latest_challenge.code_hash):
            code = c_str
            break
    assert code is not None

    # Verify with wrong code first
    wrong_resp = client.post(
        "/api/v1/auth/email-otp/verify",
        json={"code": "000000" if code != "000000" else "111111", "purpose": "login"},
    )
    assert wrong_resp.status_code == 401

    # Verify with correct code
    verify_resp = client.post(
        "/api/v1/auth/email-otp/verify",
        json={"code": code, "purpose": "login"},
    )
    assert verify_resp.status_code == 200
    assert ACCESS_COOKIE_NAME in verify_resp.cookies
    assert TWO_FA_CHALLENGE_COOKIE_NAME not in verify_resp.cookies or verify_resp.cookies[TWO_FA_CHALLENGE_COOKIE_NAME] == ""


def test_email_otp_activation_flow(client: TestClient, db: Session):
    # Create invited fellow
    invited_fellow = User(
        first_name="David",
        last_name="Invited",
        email=f"david_{uuid4().hex[:8]}@example.com",
        password_hash=None,
        role=UserRole.FELLOW,
        account_status=AccountStatus.INVITED,
        is_active=True,
        two_factor_enabled=False,
    )
    db.add(invited_fellow)
    db.flush()

    raw_token = f"raw-invite-token-{uuid4().hex}"
    invitation = UserInvitationToken(
        user_id=invited_fellow.id,
        token_hash=hash_invitation_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(invitation)
    db.commit()

    # Step 1: activate password
    act_resp = client.post(
        "/api/v1/auth/activate",
        json={
            "token": raw_token,
            "password": "NewSecurePassword123!",
            "confirm_password": "NewSecurePassword123!",
        },
    )
    assert act_resp.status_code == 200
    assert ONBOARDING_COOKIE_NAME in act_resp.cookies
    onboarding_token = act_resp.cookies[ONBOARDING_COOKIE_NAME]

    # Step 2: request Email OTP for activation
    client.cookies.set(ONBOARDING_COOKIE_NAME, onboarding_token)
    send_resp = client.post(
        "/api/v1/auth/email-otp/send",
        json={"purpose": "activation"},
    )
    assert send_resp.status_code == 200

    # Retrieve code
    challenge = db.query(EmailOTPChallenge).filter_by(user_id=invited_fellow.id, purpose="activation").first()
    assert challenge is not None
    code = None
    for candidate in range(1_000_000):
        c_str = f"{candidate:06d}"
        if verify_otp_code(invited_fellow.id, "activation", c_str, challenge.code_hash):
            code = c_str
            break
    assert code is not None

    # Step 3: verify OTP for activation
    verify_resp = client.post(
        "/api/v1/auth/email-otp/verify",
        json={"code": code, "purpose": "activation"},
    )
    assert verify_resp.status_code == 200
    assert ACCESS_COOKIE_NAME in verify_resp.cookies

    # Verify user state in db
    db.refresh(invited_fellow)
    assert invited_fellow.account_status == AccountStatus.ACTIVE
    assert invited_fellow.two_factor_enabled is True
    assert invited_fellow.preferred_auth_method == "email_otp"
    assert invited_fellow.email_verified_at is not None
