from datetime import datetime, timezone
from uuid import uuid4

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import (
    create_onboarding_token,
    hash_password,
)
from app.models.user import (
    AccountStatus,
    User,
    UserRole,
)
from app.schemas.auth import CreateFellowRequest
from app.services.auth import admin_create_fellow


def test_onboarding_token_cannot_access_normal_authenticated_api(
    client: TestClient,
    db: Session,
):
    user = User(
        first_name="Onboarding",
        last_name="Fellow",
        email=f"onboarding_{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.INVITED,
        is_active=True,
        password_set_at=datetime.now(timezone.utc),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        onboarding_token = create_onboarding_token(
            str(user.id)
        )

        response = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": (
                    f"Bearer {onboarding_token}"
                )
            },
        )

        assert response.status_code == 401

    finally:
        db.delete(user)
        db.commit()


def test_fellow_cannot_login_before_2fa_completion(
    client: TestClient,
    db: Session,
):
    email = f"no2fa_{uuid4().hex[:8]}@example.com"

    request = CreateFellowRequest(
        first_name="Test",
        last_name="Fellow",
        email=email,
    )

    user, raw_token = admin_create_fellow(
        db,
        request,
    )

    try:
        activation = client.post(
            "/api/v1/auth/activate",
            json={
                "token": raw_token,
                "password": "Pass12345!",
                "confirm_password": "Pass12345!",
            },
        )

        assert activation.status_code == 200

        db.refresh(user)

        assert user.account_status == AccountStatus.INVITED
        assert user.two_factor_enabled is False

        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "Pass12345!",
            },
        )

        assert login.status_code == 403
        assert "setup is incomplete" in (
            login.json()["detail"].lower()
        )

    finally:
        db.delete(user)
        db.commit()


def test_full_fellow_onboarding_requires_2fa(
    client: TestClient,
    db: Session,
):
    email = f"full2fa_{uuid4().hex[:8]}@example.com"

    request = CreateFellowRequest(
        first_name="Full",
        last_name="Onboarding",
        email=email,
    )

    user, raw_token = admin_create_fellow(
        db,
        request,
    )

    try:
        # Step 1: set password
        activation = client.post(
            "/api/v1/auth/activate",
            json={
                "token": raw_token,
                "password": "Pass12345!",
                "confirm_password": "Pass12345!",
            },
        )

        assert activation.status_code == 200

        activation_data = activation.json()

        assert "onboarding_token" in activation_data
        assert "access_token" not in activation_data

        onboarding_token = activation_data[
            "onboarding_token"
        ]

        # Step 2: setup TOTP
        setup = client.post(
            "/api/v1/auth/2fa/setup",
            headers={
                "Authorization": (
                    f"Bearer {onboarding_token}"
                )
            },
        )

        assert setup.status_code == 200

        setup_data = setup.json()

        assert "secret" in setup_data
        assert "totp_uri" in setup_data

        secret = setup_data["secret"]

        # Step 3: confirm TOTP
        totp_code = pyotp.TOTP(secret).now()

        confirm = client.post(
            "/api/v1/auth/2fa/confirm",
            headers={
                "Authorization": (
                    f"Bearer {onboarding_token}"
                )
            },
            json={
                "code": totp_code,
            },
        )

        assert confirm.status_code == 200

        confirm_data = confirm.json()

        assert "access_token" in confirm_data
        assert len(confirm_data["recovery_codes"]) == 8

        db.refresh(user)

        assert user.account_status == AccountStatus.ACTIVE
        assert user.two_factor_enabled is True

        # Normal access token must now work
        me = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": (
                    f"Bearer {confirm_data['access_token']}"
                )
            },
        )

        assert me.status_code == 200
        assert me.json()["email"] == email

    finally:
        db.delete(user)
        db.commit()


def test_recovery_code_is_one_time_use(
    client: TestClient,
    db: Session,
):
    email = f"recovery_{uuid4().hex[:8]}@example.com"

    request = CreateFellowRequest(
        first_name="Recovery",
        last_name="Fellow",
        email=email,
    )

    user, raw_token = admin_create_fellow(
        db,
        request,
    )

    try:
        activation = client.post(
            "/api/v1/auth/activate",
            json={
                "token": raw_token,
                "password": "Pass12345!",
                "confirm_password": "Pass12345!",
            },
        )

        onboarding_token = activation.json()[
            "onboarding_token"
        ]

        setup = client.post(
            "/api/v1/auth/2fa/setup",
            headers={
                "Authorization": (
                    f"Bearer {onboarding_token}"
                )
            },
        )

        secret = setup.json()["secret"]

        confirm = client.post(
            "/api/v1/auth/2fa/confirm",
            headers={
                "Authorization": (
                    f"Bearer {onboarding_token}"
                )
            },
            json={
                "code": pyotp.TOTP(secret).now(),
            },
        )

        assert confirm.status_code == 200

        recovery_code = confirm.json()[
            "recovery_codes"
        ][0]

        # Login to obtain challenge token
        login_1 = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "Pass12345!",
            },
        )

        assert login_1.status_code == 200
        assert login_1.json()["requires_2fa"] is True

        challenge_1 = login_1.json()[
            "challenge_token"
        ]

        first_use = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge_1,
                "code": recovery_code,
            },
        )

        assert first_use.status_code == 200
        assert "access_token" in first_use.json()

        # New login attempt, same recovery code
        login_2 = client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": "Pass12345!",
            },
        )

        challenge_2 = login_2.json()[
            "challenge_token"
        ]

        second_use = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge_2,
                "code": recovery_code,
            },
        )

        assert second_use.status_code == 401

    finally:
        db.delete(user)
        db.commit()


def test_suspended_user_cannot_complete_existing_2fa_challenge(
    client: TestClient,
    db: Session,
):
    email = f"suspended2fa_{uuid4().hex[:8]}@example.com"

    secret = pyotp.random_base32()

    user = User(
        first_name="Suspended",
        last_name="Fellow",
        email=email,
        password_hash=hash_password("Pass12345!"),
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
            json={
                "email": email,
                "password": "Pass12345!",
            },
        )

        assert login.status_code == 200

        challenge_token = login.json()[
            "challenge_token"
        ]

        # Suspend after password validation but before 2FA.
        user.account_status = AccountStatus.SUSPENDED
        db.commit()

        verify = client.post(
            "/api/v1/auth/2fa/verify",
            json={
                "challenge_token": challenge_token,
                "code": pyotp.TOTP(secret).now(),
            },
        )

        assert verify.status_code == 403

    finally:
        db.delete(user)
        db.commit()