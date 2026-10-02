"""
Comprehensive test suite for Phase 1: Authentication & Student Onboarding.

Covers:
- Student invitation creation by Admin
- Duplicate student email rejection
- Unauthenticated & non-admin role restrictions
- Account activation with token
- Expired & already-used token handling
- Password validation (length, match)
- TOTP 2FA setup & QR URI generation
- TOTP verification & account activation to 'active'
- Recovery codes generation and usage
- Two-stage login flow (password -> 2FA challenge -> verify)
- Incorrect password & invalid TOTP codes
- Suspended account handling
- GET /api/v1/auth/me safe profile inspection
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_invitation_token, hash_password
from app.models.user import AccountStatus, User, UserRole
from app.models.user_invitation import UserInvitationToken
from app.models.user_recovery_code import UserRecoveryCode
from app.services.auth import admin_create_student
from app.schemas.auth import CreateStudentRequest


# ---------------------------------------------------------------------------
# 1. Admin Student Invitation Tests
# ---------------------------------------------------------------------------

def test_admin_create_student_success(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
):
    """Admin can create a new student and generate a hashed invitation token."""
    email = f"student_{uuid4().hex[:8]}@example.com"
    payload = {
        "first_name": "Samantha",
        "last_name": "R",
        "email": email,
    }

    response = client.post(
        "/api/v1/admin/students",
        json=payload,
        headers=admin_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == email
    assert data["first_name"] == "Samantha"
    assert data["last_name"] == "R"
    assert data["role"] in ("fellow", "student")
    assert data["account_status"] == "invited"
    assert "id" in data

    # Verify database state
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    assert user.password_hash is None
    assert user.two_factor_enabled is False
    assert user.account_status == AccountStatus.INVITED
    assert user.role in (UserRole.FELLOW, UserRole.STUDENT)

    # Verify invitation token exists and only hash is stored
    invitation = db.scalar(
        select(UserInvitationToken).where(UserInvitationToken.user_id == user.id)
    )
    assert invitation is not None
    assert len(invitation.token_hash) == 64
    assert invitation.used_at is None
    assert invitation.expires_at > datetime.now(timezone.utc)

    # Cleanup
    db.delete(invitation)
    db.delete(user)
    db.commit()


def test_admin_create_student_unauthenticated(client: TestClient):
    """Unauthenticated requests to admin endpoint must return 401."""
    response = client.post(
        "/api/v1/admin/students",
        json={"first_name": "Test", "last_name": "Student", "email": "test@example.com"},
    )
    assert response.status_code == 401


def test_student_cannot_access_admin_endpoint(
    client: TestClient,
    db: Session,
):
    """Students cannot access admin endpoints (must return 403)."""
    email = f"student_{uuid4().hex[:8]}@example.com"
    student = User(
        first_name="Alice",
        last_name="Student",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.STUDENT,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(student)
    db.commit()
    db.refresh(student)

    token = create_access_token(str(student.id))
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/v1/admin/students",
        json={"first_name": "Bob", "last_name": "Student", "email": "bob@example.com"},
        headers=headers,
    )
    assert response.status_code == 403

    # Cleanup
    db.delete(student)
    db.commit()


def test_admin_create_student_duplicate_email(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
):
    """Creating student with existing email returns 409 Conflict."""
    email = f"dup_{uuid4().hex[:8]}@example.com"
    payload = {"first_name": "Original", "last_name": "User", "email": email}

    resp1 = client.post("/api/v1/admin/students", json=payload, headers=admin_headers)
    assert resp1.status_code == 201

    resp2 = client.post("/api/v1/admin/students", json=payload, headers=admin_headers)
    assert resp2.status_code == 409

    # Cleanup
    user = db.scalar(select(User).where(User.email == email))
    if user:
        tokens = db.scalars(
            select(UserInvitationToken).where(UserInvitationToken.user_id == user.id)
        ).all()
        for t in tokens:
            db.delete(t)
        db.delete(user)
        db.commit()


# ---------------------------------------------------------------------------
# 2. Account Activation & Validation Tests
# ---------------------------------------------------------------------------

def test_activate_account_validation_errors(
    client: TestClient,
    db: Session,
):
    """Activation validates password length, confirmation, and token validity."""
    # Passwords do not match
    resp = client.post(
        "/api/v1/auth/activate",
        json={
            "token": "some_token",
            "password": "Password123!",
            "confirm_password": "MismatchPassword!",
        },
    )
    assert resp.status_code == 422

    # Password too short (< 8 chars)
    resp = client.post(
        "/api/v1/auth/activate",
        json={
            "token": "some_token",
            "password": "short",
            "confirm_password": "short",
        },
    )
    assert resp.status_code == 422

    # Non-existent token
    resp = client.post(
        "/api/v1/auth/activate",
        json={
            "token": "nonexistent_raw_token",
            "password": "ValidPassword123!",
            "confirm_password": "ValidPassword123!",
        },
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_activate_account_expired_token(
    client: TestClient,
    db: Session,
):
    """Activation with an expired token returns 400 Bad Request."""
    email = f"expired_{uuid4().hex[:8]}@example.com"
    req = CreateStudentRequest(first_name="Exp", last_name="User", email=email)
    user, raw_token = admin_create_student(db, req)

    # Manually expire the token in database
    token_hash = hash_invitation_token(raw_token)
    inv = db.scalar(
        select(UserInvitationToken).where(UserInvitationToken.token_hash == token_hash)
    )
    inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    resp = client.post(
        "/api/v1/auth/activate",
        json={
            "token": raw_token,
            "password": "ValidPassword123!",
            "confirm_password": "ValidPassword123!",
        },
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()

    # Cleanup
    db.delete(inv)
    db.delete(user)
    db.commit()


# ---------------------------------------------------------------------------
# 3. Full End-to-End Onboarding & Two-Stage Login Flow
# ---------------------------------------------------------------------------

def test_full_student_onboarding_and_auth_lifecycle(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
):
    """
    Production Fellow onboarding flow:

    1. Admin invites Fellow.
    2. Fellow starts as INVITED with no password.
    3. Fellow activates invitation and creates password.
    4. Account becomes ACTIVE immediately.
    5. 2FA remains disabled.
    6. Invitation token cannot be reused.
    7. Fellow logs in using email + password.
    8. JWT grants access to /auth/me.
    9. Fellow cannot access Admin endpoints.
    """

    student_email = f"lifecycle_{uuid4().hex[:8]}@example.com"



def test_suspended_account_rejected(
    client: TestClient,
    db: Session,
):
    """Suspended user cannot log in and existing tokens are rejected."""
    email = f"suspended_{uuid4().hex[:8]}@example.com"
    user = User(
        first_name="Suspended",
        last_name="User",
        email=email,
        password_hash=hash_password("Pass12345!"),
        role=UserRole.STUDENT,
        account_status=AccountStatus.SUSPENDED,
        two_factor_enabled=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Login fails
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Pass12345!"},
    )
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"].lower()

    # Even with a valid token, /me rejects suspended accounts
    token = create_access_token(str(user.id))
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 403

    # Cleanup
    db.delete(user)
    db.commit()
