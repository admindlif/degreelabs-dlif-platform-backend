import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth_cookies import (
    ACCESS_COOKIE_NAME,
    ONBOARDING_COOKIE_NAME,
    TWO_FA_CHALLENGE_COOKIE_NAME,
)
from app.core.config import settings
from app.core.security import create_onboarding_token, hash_password
from app.models.user import AccountStatus, User, UserRole
from app.models.user_recovery_code import UserRecoveryCode
from app.schemas.auth import CreateFellowRequest
from app.services.auth import admin_create_fellow


PASSWORD = "Pass12345!"


def _create_invited_fellow(db: Session, prefix: str) -> tuple[User, str]:
    return admin_create_fellow(
        db,
        CreateFellowRequest(
            first_name="Test",
            last_name="Fellow",
            email=f"{prefix}_{uuid4().hex[:8]}@example.com",
        ),
    )


def _activate(client: TestClient, raw_token: str):
    return client.post(
        "/api/v1/auth/activate",
        json={
            "token": raw_token,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )


def _complete_onboarding(
    client: TestClient,
    db: Session,
    user: User,
) -> list[str]:
    setup = client.post("/api/v1/auth/2fa/setup")
    assert setup.status_code == 200
    db.refresh(user)
    assert user.totp_secret is not None

    confirm = client.post(
        "/api/v1/auth/2fa/confirm",
        json={"code": pyotp.TOTP(user.totp_secret).now()},
    )
    assert confirm.status_code == 200
    return confirm.json()["recovery_codes"]


def test_onboarding_token_cannot_access_normal_authenticated_api(
    client: TestClient,
    db: Session,
):
    user = User(
        first_name="Onboarding",
        last_name="Fellow",
        email=f"onboarding_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password(PASSWORD),
        role=UserRole.FELLOW,
        account_status=AccountStatus.INVITED,
        is_active=True,
        password_set_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        response = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {create_onboarding_token(str(user.id))}"
            },
        )
        assert response.status_code == 401
    finally:
        db.delete(user)
        db.commit()


def test_activation_sets_http_only_onboarding_cookie_and_is_single_use(
    client: TestClient,
    db: Session,
):
    user, raw_token = _create_invited_fellow(db, "activation")

    try:
        activation = _activate(client, raw_token)
        assert activation.status_code == 200
        assert "onboarding_token" not in activation.json()
        assert "access_token" not in activation.json()
        assert activation.headers["cache-control"] == "no-store"

        set_cookie = activation.headers["set-cookie"].lower()
        assert f"{ONBOARDING_COOKIE_NAME}=" in set_cookie
        assert "httponly" in set_cookie
        assert "path=/api/v1/auth" in set_cookie
        assert client.cookies.get(ONBOARDING_COOKIE_NAME) is not None

        db.refresh(user)
        assert user.password_hash is not None
        assert user.account_status == AccountStatus.INVITED

        reused = _activate(client, raw_token)
        assert reused.status_code == 400
    finally:
        db.delete(user)
        db.commit()


def test_two_fa_setup_requires_cookie_and_qr_is_non_cacheable_png(
    client: TestClient,
    db: Session,
):
    user, raw_token = _create_invited_fellow(db, "qr")

    try:
        without_cookie = client.post("/api/v1/auth/2fa/setup")
        assert without_cookie.status_code == 401

        assert _activate(client, raw_token).status_code == 200
        setup = client.post("/api/v1/auth/2fa/setup")
        assert setup.status_code == 200
        assert setup.headers["cache-control"] == "no-store"
        assert setup.json()["qr_code_url"] == "/api/v1/auth/2fa/qr"
        assert "secret" not in setup.json()
        assert "totp_uri" not in setup.json()
        assert "otpauth://" not in setup.text

        db.refresh(user)
        assert user.totp_secret is not None
        assert user.totp_secret not in setup.text

        qr = client.get("/api/v1/auth/2fa/qr")
        assert qr.status_code == 200
        assert qr.headers["content-type"] == "image/png"
        assert qr.headers["cache-control"] == "no-store"
        assert qr.content.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        db.delete(user)
        db.commit()


def test_two_fa_confirmation_activates_and_stores_hashed_recovery_codes(
    client: TestClient,
    db: Session,
):
    user, raw_token = _create_invited_fellow(db, "confirmation")

    try:
        assert _activate(client, raw_token).status_code == 200
        assert client.post("/api/v1/auth/2fa/setup").status_code == 200
        db.refresh(user)
        assert user.totp_secret is not None

        correct_code = pyotp.TOTP(user.totp_secret).now()
        wrong_code = str((int(correct_code) + 1) % 1_000_000).zfill(6)
        rejected = client.post(
            "/api/v1/auth/2fa/confirm",
            json={"code": wrong_code},
        )
        assert rejected.status_code == 422
        db.refresh(user)
        assert user.account_status == AccountStatus.INVITED

        confirmed = client.post(
            "/api/v1/auth/2fa/confirm",
            json={"code": correct_code},
        )
        assert confirmed.status_code == 200
        body = confirmed.json()
        assert "access_token" not in body
        assert len(body["recovery_codes"]) == 8
        assert client.cookies.get(ACCESS_COOKIE_NAME) is not None
        assert client.cookies.get(ONBOARDING_COOKIE_NAME) is None

        db.refresh(user)
        assert user.account_status == AccountStatus.ACTIVE
        assert user.two_factor_enabled is True

        stored = db.scalars(
            select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)
        ).all()
        assert len(stored) == 8
        assert {item.code_hash for item in stored} == {
            hashlib.sha256(code.encode()).hexdigest()
            for code in body["recovery_codes"]
        }
        assert not {
            item.code_hash for item in stored
        }.intersection(body["recovery_codes"])

        repeated = client.post(
            "/api/v1/auth/2fa/confirm",
            json={"code": correct_code},
        )
        assert repeated.status_code == 401
    finally:
        db.delete(user)
        db.commit()


def test_login_totp_sets_access_cookie_and_me_uses_it(
    client: TestClient,
    db: Session,
):
    user, raw_token = _create_invited_fellow(db, "login")

    try:
        assert _activate(client, raw_token).status_code == 200
        _complete_onboarding(client, db, user)
        client.post("/api/v1/auth/logout")

        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert login.status_code == 200
        assert login.json() == {"requires_2fa": True}
        assert "challenge_token" not in login.json()
        assert client.cookies.get(TWO_FA_CHALLENGE_COOKIE_NAME) is not None

        db.refresh(user)
        assert user.totp_secret is not None
        invalid = client.post(
            "/api/v1/auth/2fa/verify",
            json={"code": "000000"},
        )
        if invalid.status_code == 200:  # practically impossible TOTP collision
            client.post("/api/v1/auth/logout")
            login = client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": PASSWORD},
            )
            assert login.status_code == 200
        else:
            assert invalid.status_code == 401

        verified = client.post(
            "/api/v1/auth/2fa/verify",
            json={"code": pyotp.TOTP(user.totp_secret).now()},
        )
        assert verified.status_code == 200
        assert verified.json() == {"message": "Authentication successful."}
        assert "access_token" not in verified.json()
        assert client.cookies.get(ACCESS_COOKIE_NAME) is not None
        assert client.cookies.get(TWO_FA_CHALLENGE_COOKIE_NAME) is None

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == user.email
    finally:
        db.delete(user)
        db.commit()


def test_login_without_two_fa_sets_access_cookie_without_returning_token(
    client: TestClient,
    db: Session,
):
    user = User(
        first_name="Program",
        last_name="Manager",
        email=f"manager_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password(PASSWORD),
        role=UserRole.PROGRAM_MANAGER,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert login.status_code == 200
        assert login.json() == {"requires_2fa": False}
        assert "access_token" not in login.json()
        assert client.cookies.get(ACCESS_COOKIE_NAME) is not None
        assert client.cookies.get(TWO_FA_CHALLENGE_COOKIE_NAME) is None
        db.refresh(user)
        assert user.last_login_at is not None
    finally:
        db.delete(user)
        db.commit()


def test_recovery_code_is_one_time_use(
    client: TestClient,
    db: Session,
):
    user, raw_token = _create_invited_fellow(db, "recovery")

    try:
        assert _activate(client, raw_token).status_code == 200
        recovery_code = _complete_onboarding(client, db, user)[0]
        client.post("/api/v1/auth/logout")

        for expected_status in (200, 401):
            login = client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": PASSWORD},
            )
            assert login.status_code == 200
            verified = client.post(
                "/api/v1/auth/2fa/verify",
                json={"code": recovery_code},
            )
            assert verified.status_code == expected_status
            client.post("/api/v1/auth/logout")
    finally:
        db.delete(user)
        db.commit()


def test_invalid_and_expired_access_cookies_return_401(
    client: TestClient,
    db: Session,
):
    user = User(
        first_name="Expired",
        last_name="Cookie",
        email=f"expired_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password(PASSWORD),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=True,
        totp_secret=pyotp.random_base32(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
                "type": "access",
            },
            settings.secret_key,
            algorithm="HS256",
        )
        for token in ("not-a-jwt", expired):
            client.cookies.set(ACCESS_COOKIE_NAME, token, path="/api/v1")
            assert client.get("/api/v1/auth/me").status_code == 401
    finally:
        db.delete(user)
        db.commit()


def test_suspended_user_cannot_complete_existing_2fa_challenge(
    client: TestClient,
    db: Session,
):
    secret = pyotp.random_base32()
    user = User(
        first_name="Suspended",
        last_name="Fellow",
        email=f"suspended_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password(PASSWORD),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=True,
        totp_secret=secret,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert login.status_code == 200
        assert client.cookies.get(TWO_FA_CHALLENGE_COOKIE_NAME) is not None

        user.account_status = AccountStatus.SUSPENDED
        db.commit()
        verify = client.post(
            "/api/v1/auth/2fa/verify",
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 403
    finally:
        db.delete(user)
        db.commit()


def test_logout_clears_all_auth_cookies(client: TestClient):
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    deletion_headers = response.headers.get_list("set-cookie")
    for cookie_name in (
        ACCESS_COOKIE_NAME,
        ONBOARDING_COOKIE_NAME,
        TWO_FA_CHALLENGE_COOKIE_NAME,
    ):
        matching = [
            header for header in deletion_headers if header.startswith(f"{cookie_name}=")
        ]
        assert len(matching) == 1
        assert "Max-Age=0" in matching[0]
