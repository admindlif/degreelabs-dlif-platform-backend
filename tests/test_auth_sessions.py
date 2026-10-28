from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db.session as db_session_module
from app.core.auth_cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, hash_password
from app.models.auth_session import AuthSession, AuthSessionRefreshToken
from app.models.user import AccountStatus, User, UserRole
from app.services.auth_session import (
    ConcurrentRefreshError,
    RefreshTokenReuseError,
    SessionExpiredError,
    create_authenticated_session,
    ensure_session_active,
    hash_refresh_token,
    refresh_authenticated_session,
    revoke_session,
)


PASSWORD = "SessionSecurity123!"


def _create_user(
    db: Session,
    *,
    role: UserRole = UserRole.FELLOW,
    two_factor_enabled: bool = True,
) -> User:
    now = datetime.now(timezone.utc)
    user = User(
        first_name="Session",
        last_name=role.value,
        email=f"session_{role.value}_{uuid4().hex[:10]}@example.com",
        password_hash=hash_password(PASSWORD),
        role=role,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        two_factor_enabled=two_factor_enabled,
        totp_secret=pyotp.random_base32() if two_factor_enabled else None,
        password_set_at=now - timedelta(days=1),
        email_verified_at=now - timedelta(days=1),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_session_cookies(client: TestClient, tokens) -> None:
    client.cookies.set(
        ACCESS_COOKIE_NAME,
        tokens.access_token,
        domain="testserver.local",
        path="/api/v1",
    )
    client.cookies.set(
        REFRESH_COOKIE_NAME,
        tokens.refresh_token,
        domain="testserver.local",
        path="/api/v1/auth",
    )


def test_access_token_lifetime_is_fifteen_minutes(
    monkeypatch: pytest.MonkeyPatch,
):
    issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "access_token_expire_minutes", 15)

    token = create_access_token(str(uuid4()), now=issued_at)
    payload = decode_access_token(token, verify_expiration=False)

    assert float(payload["exp"]) - float(payload["iat"]) == 15 * 60


def test_password_and_mfa_login_creates_hashed_server_session(
    client: TestClient,
    db: Session,
):
    user = _create_user(db)
    try:
        stage_one = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert stage_one.status_code == 200
        assert stage_one.json() == {"requires_2fa": True}

        verified = client.post(
            "/api/v1/auth/2fa/verify",
            json={"code": pyotp.TOTP(user.totp_secret).now()},
        )
        assert verified.status_code == 200

        refresh_cookie_header = next(
            value
            for value in verified.headers.get_list("set-cookie")
            if value.startswith(f"{REFRESH_COOKIE_NAME}=")
        ).lower()
        assert "httponly" in refresh_cookie_header
        assert "path=/api/v1/auth" in refresh_cookie_header
        assert f"samesite={settings.auth_cookie_samesite}" in refresh_cookie_header
        assert ("secure" in refresh_cookie_header) is (
            settings.effective_auth_cookie_secure
        )

        raw_refresh = client.cookies.get(REFRESH_COOKIE_NAME)
        access_token = client.cookies.get(ACCESS_COOKIE_NAME)
        assert raw_refresh
        assert access_token

        payload = decode_access_token(access_token)
        assert payload["sub"] == str(user.id)
        assert payload.get("sid")

        auth_session = db.get(AuthSession, payload["sid"])
        assert auth_session is not None
        assert auth_session.user_id == user.id
        assert auth_session.current_refresh_token_hash == hash_refresh_token(raw_refresh)
        assert raw_refresh != auth_session.current_refresh_token_hash
        assert auth_session.revoked_at is None

        history = db.scalar(
            select(AuthSessionRefreshToken).where(
                AuthSessionRefreshToken.session_id == auth_session.id,
                AuthSessionRefreshToken.generation == 0,
            )
        )
        assert history is not None
        assert history.token_hash == auth_session.current_refresh_token_hash
    finally:
        db.delete(user)
        db.commit()


def test_expired_access_token_can_refresh_without_ending_valid_session(
    client: TestClient,
    db: Session,
):
    user = _create_user(db)
    issued_at = datetime.now(timezone.utc) - timedelta(
        minutes=settings.access_token_expire_minutes + 1
    )
    try:
        tokens = create_authenticated_session(db, user, now=issued_at)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(tokens.access_token)

        _set_session_cookies(client, tokens)
        assert client.get("/api/v1/auth/me").status_code == 401

        refreshed = client.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json() == {"message": "Session refreshed successfully."}
        assert client.get("/api/v1/auth/me").status_code == 200

        db.refresh(tokens.session)
        assert tokens.session.revoked_at is None
        assert tokens.session.refresh_generation == 1
    finally:
        db.delete(user)
        db.commit()


def test_inactivity_expires_at_exactly_24_hours(db: Session):
    user = _create_user(db)
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        tokens = create_authenticated_session(db, user, now=started_at)
        with pytest.raises(SessionExpiredError) as caught:
            refresh_authenticated_session(
                db,
                tokens.refresh_token,
                now=started_at + timedelta(hours=24),
            )
        assert caught.value.reason == "inactivity_expired"
        db.refresh(tokens.session)
        assert tokens.session.revoked_reason == "inactivity_expired"
    finally:
        db.delete(user)
        db.commit()


@pytest.mark.parametrize(
    ("role", "absolute_hours"),
    [
        (UserRole.FELLOW, 168),
        (UserRole.STUDENT, 168),
        (UserRole.MENTOR, 72),
        (UserRole.ADMIN, 24),
        (UserRole.SUPER_ADMIN, 24),
        (UserRole.PROGRAM_MANAGER, 24),
    ],
)
def test_role_specific_absolute_expiration(
    db: Session,
    role: UserRole,
    absolute_hours: int,
):
    user = _create_user(db, role=role)
    started_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    try:
        tokens = create_authenticated_session(db, user, now=started_at)
        assert tokens.session.absolute_expires_at == started_at + timedelta(
            hours=absolute_hours
        )

        # Simulate genuine activity near the absolute boundary. It may extend
        # inactivity but must never move the absolute deadline.
        tokens.session.last_activity_at = (
            started_at + timedelta(hours=absolute_hours) - timedelta(minutes=1)
        )
        db.commit()

        with pytest.raises(SessionExpiredError) as caught:
            ensure_session_active(
                db,
                tokens.session,
                user,
                now=started_at + timedelta(hours=absolute_hours),
            )
        assert caught.value.reason == "absolute_expired"
        assert tokens.session.absolute_expires_at == started_at + timedelta(
            hours=absolute_hours
        )
    finally:
        db.delete(user)
        db.commit()


def test_logout_revokes_server_session(client: TestClient, db: Session):
    user = _create_user(db)
    try:
        tokens = create_authenticated_session(db, user)
        _set_session_cookies(client, tokens)
        logged_out = client.post("/api/v1/auth/logout")
        assert logged_out.status_code == 200
        assert client.cookies.get(ACCESS_COOKIE_NAME) is None
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None

        db.refresh(tokens.session)
        assert tokens.session.revoked_at is not None
        assert tokens.session.revoked_reason == "user_logout"
    finally:
        db.delete(user)
        db.commit()


def test_refresh_rotation_and_replay_detection(db: Session):
    user = _create_user(db)
    started_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    try:
        initial = create_authenticated_session(db, user, now=started_at)
        rotated_at = started_at + timedelta(minutes=1)
        rotated = refresh_authenticated_session(
            db,
            initial.refresh_token,
            now=rotated_at,
        )
        assert rotated.refresh_token != initial.refresh_token
        assert rotated.session.refresh_generation == 1

        replay_at = rotated_at + timedelta(
            seconds=settings.session_refresh_race_grace_seconds + 1
        )
        with pytest.raises(RefreshTokenReuseError):
            refresh_authenticated_session(
                db,
                initial.refresh_token,
                now=replay_at,
            )

        db.refresh(initial.session)
        assert initial.session.revoked_reason == "refresh_token_reuse"
    finally:
        db.delete(user)
        db.commit()


def test_http_refresh_race_returns_recoverable_409_without_revocation(
    client: TestClient,
    db: Session,
):
    user = _create_user(db)
    try:
        initial = create_authenticated_session(db, user)
        _set_session_cookies(client, initial)

        winner = client.post("/api/v1/auth/refresh")
        assert winner.status_code == 200
        winning_refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
        assert winning_refresh_token
        assert winning_refresh_token != initial.refresh_token

        # Model a request that left another tab with the just-consumed token.
        client.cookies.set(
            REFRESH_COOKIE_NAME,
            initial.refresh_token,
            domain="testserver.local",
            path="/api/v1/auth",
        )
        loser = client.post("/api/v1/auth/refresh")
        assert loser.status_code == 409
        assert loser.headers["Retry-After"] == "1"
        assert client.cookies.get(REFRESH_COOKIE_NAME) == initial.refresh_token

        db.refresh(initial.session)
        assert initial.session.revoked_at is None

        # The shared cookie written by the winning request remains sufficient
        # to recover via the status endpoint without issuing another refresh.
        client.cookies.set(
            REFRESH_COOKIE_NAME,
            winning_refresh_token,
            domain="testserver.local",
            path="/api/v1/auth",
        )
        recovered = client.get("/api/v1/auth/session")
        assert recovered.status_code == 200
        assert recovered.json()["authenticated"] is True
    finally:
        db.delete(user)
        db.commit()


def test_session_status_uses_valid_refresh_after_access_token_expiry(
    client: TestClient,
    db: Session,
):
    user = _create_user(db)
    issued_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    try:
        tokens = create_authenticated_session(db, user, now=issued_at)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(tokens.access_token)
        _set_session_cookies(client, tokens)

        response = client.get("/api/v1/auth/session")

        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert datetime.fromisoformat(body["access_expires_at"]) < datetime.now(
            timezone.utc
        )
    finally:
        db.delete(user)
        db.commit()


def test_revoked_session_cannot_be_refreshed(client: TestClient, db: Session):
    user = _create_user(db)
    try:
        tokens = create_authenticated_session(db, user)
        revoke_session(db, tokens.session, reason="security_revocation")
        db.commit()
        _set_session_cookies(client, tokens)

        response = client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        assert client.cookies.get(ACCESS_COOKIE_NAME) is None
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
    finally:
        db.delete(user)
        db.commit()


def test_expired_session_cannot_be_refreshed(client: TestClient, db: Session):
    user = _create_user(db)
    issued_at = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        tokens = create_authenticated_session(db, user, now=issued_at)
        _set_session_cookies(client, tokens)

        response = client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        db.refresh(tokens.session)
        assert tokens.session.revoked_reason == "inactivity_expired"
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
    finally:
        db.delete(user)
        db.commit()


def test_concurrent_refresh_rotates_once_without_revocation(db: Session):
    user = _create_user(db)
    started_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    initial = create_authenticated_session(db, user, now=started_at)
    refresh_at = started_at + timedelta(minutes=1)

    def worker() -> str:
        thread_db = db_session_module.SessionLocal()
        try:
            try:
                refresh_authenticated_session(
                    thread_db,
                    initial.refresh_token,
                    now=refresh_at,
                )
                return "rotated"
            except ConcurrentRefreshError:
                return "concurrent"
        finally:
            thread_db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))

        assert sorted(results) == ["concurrent", "rotated"]
        db.expire_all()
        session = db.get(AuthSession, initial.session.id)
        assert session is not None
        assert session.refresh_generation == 1
        assert session.revoked_at is None
    finally:
        db.delete(user)
        db.commit()


def test_session_status_does_not_count_as_activity(client: TestClient, db: Session):
    user = _create_user(db)
    issued_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    try:
        tokens = create_authenticated_session(db, user, now=issued_at)
        original_activity = tokens.session.last_activity_at
        _set_session_cookies(client, tokens)

        status_response = client.get("/api/v1/auth/session")
        assert status_response.status_code == 200
        assert status_response.json()["authenticated"] is True
        db.refresh(tokens.session)
        assert tokens.session.last_activity_at == original_activity

        activity_response = client.post("/api/v1/auth/activity")
        assert activity_response.status_code == 200
        db.refresh(tokens.session)
        assert tokens.session.last_activity_at > original_activity
        assert tokens.session.absolute_expires_at == (
            issued_at + timedelta(hours=settings.session_absolute_fellow_hours)
        )
    finally:
        db.delete(user)
        db.commit()


def test_unapproved_origin_is_rejected(client: TestClient, db: Session):
    user = _create_user(db)
    try:
        blocked = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"email": user.email, "password": PASSWORD},
        )
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "Request origin is not allowed."

        allowed = client.post(
            "/api/v1/auth/login",
            headers={"Origin": settings.mentor_frontend_base_url},
            json={"email": user.email, "password": PASSWORD},
        )
        assert allowed.status_code == 200

        missing_origin = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ""},
        )
        assert missing_origin.status_code == 403
        assert missing_origin.json()["detail"] == (
            "Origin header is required for cookie-authenticated requests."
        )
    finally:
        db.delete(user)
        db.commit()


def test_account_suspension_revokes_existing_session(client: TestClient, db: Session):
    user = _create_user(db)
    try:
        tokens = create_authenticated_session(db, user)
        _set_session_cookies(client, tokens)
        user.account_status = AccountStatus.SUSPENDED
        db.commit()

        denied = client.get("/api/v1/auth/me")
        assert denied.status_code == 403
        db.refresh(tokens.session)
        assert tokens.session.revoked_reason == "account_suspended"
    finally:
        db.delete(user)
        db.commit()


def test_login_rate_limit_is_shared_and_returns_retry_after(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = _create_user(db)
    monkeypatch.setattr(settings, "auth_login_attempt_limit", 2)
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": "wrong-password"},
            )
            assert response.status_code == 401

        blocked = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
    finally:
        db.delete(user)
        db.commit()


def test_privileged_mfa_enrollment_rollout_creates_session_after_confirm(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = _create_user(
        db,
        role=UserRole.ADMIN,
        two_factor_enabled=False,
    )
    monkeypatch.setattr(settings, "privileged_mfa_enforcement_enabled", True)
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert login.status_code == 200
        assert login.json() == {
            "requires_2fa": False,
            "requires_2fa_setup": True,
        }
        assert client.cookies.get(ACCESS_COOKIE_NAME) is None

        setup = client.post("/api/v1/auth/2fa/setup")
        assert setup.status_code == 200
        db.refresh(user)
        confirmed = client.post(
            "/api/v1/auth/2fa/confirm",
            json={"code": pyotp.TOTP(user.totp_secret).now()},
        )
        assert confirmed.status_code == 200
        assert client.cookies.get(ACCESS_COOKIE_NAME)
        assert client.cookies.get(REFRESH_COOKIE_NAME)
    finally:
        db.delete(user)
        db.commit()
