import pytest
from app.core.config import settings



@pytest.fixture(autouse=True)

def use_logging_email(monkeypatch):

    monkeypatch.setattr(settings, "email_backend", "logging")



from datetime import datetime, timedelta, timezone

from uuid import uuid4



import pytest

from fastapi.testclient import TestClient

from sqlalchemy.orm import Session



from app.core.auth_cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME

from app.core.security import hash_password, verify_password

from app.models.password_recovery import PasswordRecoveryRequest

from app.models.user import AccountStatus, User, UserRole

from app.services.password_recovery import (

    hash_recovery_code,

    hash_reset_token,

)
from app.services.auth_session import create_authenticated_session





@pytest.fixture

def fellow_user(db: Session) -> User:

    user = User(

        first_name="Jane",

        last_name="Fellow",

        email=f"fellow_{uuid4().hex[:8]}@example.com",

        password_hash=hash_password("OldPassword123!"),

        role=UserRole.FELLOW,

        account_status=AccountStatus.ACTIVE,

        two_factor_enabled=True,

        totp_secret="JBSWY3DPEHPK3PXP",

        password_set_at=datetime.now(timezone.utc) - timedelta(days=1),

        is_active=True,

    )

    db.add(user)

    db.commit()

    db.refresh(user)

    return user





@pytest.fixture

def mentor_user(db: Session) -> User:

    user = User(

        first_name="Marcus",

        last_name="Mentor",

        email=f"mentor_{uuid4().hex[:8]}@example.com",

        password_hash=hash_password("MentorPass123!"),

        role=UserRole.MENTOR,

        account_status=AccountStatus.ACTIVE,

        two_factor_enabled=True,

        totp_secret="JBSWY3DPEHPK3PXP",

        password_set_at=datetime.now(timezone.utc) - timedelta(days=1),

        is_active=True,

    )

    db.add(user)

    db.commit()

    db.refresh(user)

    return user





def test_request_recovery_fellow_success(client: TestClient, db: Session, fellow_user: User):

    res = client.post(

        "/api/v1/auth/password-reset/request",

        json={"email": fellow_user.email, "portal": "fellow"},

    )

    assert res.status_code == 200

    data = res.json()

    assert "recovery_request_id" in data

    assert "eligible account exists" in data["message"]



    req = db.query(PasswordRecoveryRequest).filter_by(user_id=fellow_user.id).first()

    assert req is not None

    assert req.portal == "fellow"

    assert req.consumed_at is None

    assert req.verified_at is None

    assert len(req.code_hash) == 64





def test_request_recovery_mentor_success(client: TestClient, db: Session, mentor_user: User):

    res = client.post(

        "/api/v1/auth/password-reset/request",

        json={"email": mentor_user.email, "portal": "mentor"},

    )

    assert res.status_code == 200

    data = res.json()

    assert "recovery_request_id" in data



    req = db.query(PasswordRecoveryRequest).filter_by(user_id=mentor_user.id).first()

    assert req is not None

    assert req.portal == "mentor"





def test_request_recovery_cross_portal_isolation(client: TestClient, db: Session, fellow_user: User):

    # Fellow tries to reset via mentor portal

    res = client.post(

        "/api/v1/auth/password-reset/request",

        json={"email": fellow_user.email, "portal": "mentor"},

    )

    assert res.status_code == 200

    # Must return generic message without creating DB recovery request

    req = db.query(PasswordRecoveryRequest).filter_by(user_id=fellow_user.id).first()

    assert req is None





def test_request_recovery_non_existent_email(client: TestClient, db: Session):

    res = client.post(

        "/api/v1/auth/password-reset/request",

        json={"email": "nonexistent@example.com", "portal": "fellow"},

    )

    assert res.status_code == 200

    data = res.json()

    assert "recovery_request_id" in data

    assert "eligible account exists" in data["message"]





def test_verify_code_success_and_expiry(client: TestClient, db: Session, fellow_user: User):

    now = datetime.now(timezone.utc)

    code = "87654321"

    code_hash = hash_recovery_code(code)



    req = PasswordRecoveryRequest(

        user_id=fellow_user.id,

        portal="fellow",

        code_hash=code_hash,

        expires_at=now + timedelta(minutes=10),

        attempt_count=0,

        last_sent_at=now,

    )

    db.add(req)

    db.commit()

    db.refresh(req)



    # Wrong code

    res_wrong = client.post(

        "/api/v1/auth/password-reset/verify",

        json={"recovery_request_id": str(req.id), "code": "11111111"},

    )

    assert res_wrong.status_code == 400

    db.refresh(req)

    assert req.attempt_count == 1



    # Correct code

    res_ok = client.post(

        "/api/v1/auth/password-reset/verify",

        json={"recovery_request_id": str(req.id), "code": code},

    )

    assert res_ok.status_code == 200

    data = res_ok.json()

    assert "reset_authorization" in data

    reset_auth = data["reset_authorization"]



    db.refresh(req)

    assert req.verified_at is not None

    assert req.reset_token_hash == hash_reset_token(reset_auth)



    # Re-verify same request should fail

    res_reverify = client.post(

        "/api/v1/auth/password-reset/verify",

        json={"recovery_request_id": str(req.id), "code": code},

    )

    assert res_reverify.status_code == 400





def test_verify_code_attempt_limit(client: TestClient, db: Session, fellow_user: User):

    now = datetime.now(timezone.utc)

    req = PasswordRecoveryRequest(

        user_id=fellow_user.id,

        portal="fellow",

        code_hash=hash_recovery_code("12345678"),

        expires_at=now + timedelta(minutes=10),

        attempt_count=4,

        last_sent_at=now,

    )

    db.add(req)

    db.commit()

    db.refresh(req)



    # 5th attempt (fails)

    res5 = client.post(

        "/api/v1/auth/password-reset/verify",

        json={"recovery_request_id": str(req.id), "code": "00000000"},

    )

    assert res5.status_code in (400, 429)



    # 6th attempt (blocked with 429)

    res6 = client.post(

        "/api/v1/auth/password-reset/verify",

        json={"recovery_request_id": str(req.id), "code": "12345678"},

    )

    assert res6.status_code == 429





def test_confirm_password_reset_and_session_revocation(

    client: TestClient,

    db: Session,

    fellow_user: User,

):

    now = datetime.now(timezone.utc)

    # 1. Issue an access token for fellow_user before password reset

    # Set password_set_at in the past

    fellow_user.password_set_at = datetime.now(timezone.utc) - timedelta(hours=1)

    db.commit()

    db.refresh(fellow_user)

    # Create a server-managed session before the password reset.

    server_tokens = create_authenticated_session(db, fellow_user)

    old_access_token = server_tokens.access_token



    # Verify that the old access token works on /me

    client.cookies.set(ACCESS_COOKIE_NAME, old_access_token)
    client.cookies.set(
        REFRESH_COOKIE_NAME,
        server_tokens.refresh_token,
        path="/api/v1/auth",
    )

    res_me = client.get("/api/v1/auth/me")

    assert res_me.status_code == 200



    # 2. Setup verified recovery request

    raw_reset_token = "secure_reset_token_test_1234567890"

    req = PasswordRecoveryRequest(

        user_id=fellow_user.id,

        portal="fellow",

        code_hash=hash_recovery_code("12345678"),

        expires_at=now + timedelta(minutes=10),

        verified_at=now,

        reset_token_hash=hash_reset_token(raw_reset_token),

        reset_token_expires_at=now + timedelta(minutes=15),

        attempt_count=1,

        last_sent_at=now,

    )

    db.add(req)

    db.commit()



    # 3. Confirm password reset

    new_password = "NewStrongPassword2026!"

    res_confirm = client.post(

        "/api/v1/auth/password-reset/confirm",

        json={

            "reset_authorization": raw_reset_token,

            "new_password": new_password,

            "confirm_password": new_password,

        },

    )

    assert res_confirm.status_code == 200

    assert "updated successfully" in res_confirm.json()["message"]



    # 4. Verify user password was changed in DB

    db.refresh(fellow_user)

    assert verify_password(new_password, fellow_user.password_hash)

    assert not verify_password("OldPassword123!", fellow_user.password_hash)

    assert fellow_user.two_factor_enabled is True  # 2FA preserved!



    # 5. Session revocation test: old access token must now be rejected

    res_me_after = client.get("/api/v1/auth/me")

    assert res_me_after.status_code == 401

    db.refresh(server_tokens.session)

    assert server_tokens.session.revoked_reason == "password_reset"



    # 6. Reusing reset_authorization should fail

    res_reused = client.post(

        "/api/v1/auth/password-reset/confirm",

        json={

            "reset_authorization": raw_reset_token,

            "new_password": new_password,

            "confirm_password": new_password,

        },

    )

    assert res_reused.status_code == 400





def test_confirm_password_validation_rules(client: TestClient, db: Session, fellow_user: User):

    now = datetime.now(timezone.utc)

    raw_token = "token_validation_test_12345"

    req = PasswordRecoveryRequest(

        user_id=fellow_user.id,

        portal="fellow",

        code_hash=hash_recovery_code("12345678"),

        expires_at=now + timedelta(minutes=10),

        verified_at=now,

        reset_token_hash=hash_reset_token(raw_token),

        reset_token_expires_at=now + timedelta(minutes=15),

        last_sent_at=now,

    )

    db.add(req)

    db.commit()



    # Mismatched passwords

    res_mismatch = client.post(

        "/api/v1/auth/password-reset/confirm",

        json={

            "reset_authorization": raw_token,

            "new_password": "NewPassword123!",

            "confirm_password": "DifferentPassword123!",

        },

    )

    assert res_mismatch.status_code == 422



    # Too short (< 8 chars)

    res_short = client.post(

        "/api/v1/auth/password-reset/confirm",

        json={

            "reset_authorization": raw_token,

            "new_password": "short",

            "confirm_password": "short",

        },

    )

    assert res_short.status_code == 422
