"""Local-only Playwright data fixture and controlled-clock helper.

This script intentionally has no HTTP route. It refuses non-loopback databases
and database names that do not end in ``_e2e_test``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyotp
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from e2e_database import configure_e2e_environment


configure_e2e_environment()

from app.core.security import hash_password, hash_recovery_code  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: F401,E402
from app.models.auth_session import (  # noqa: E402
    AuthAuditEvent,
    AuthRateLimitBucket,
    AuthSession,
)
from app.models.password_recovery import PasswordRecoveryRequest  # noqa: E402
from app.models.user import AccountStatus, User, UserRole  # noqa: E402
from app.models.user_recovery_code import UserRecoveryCode  # noqa: E402


PASSWORD = "E2e-Local-Password-123!"
RESET_PASSWORD = "E2e-Reset-Password-456!"
RESET_CODE = "24681357"
RECOVERY_CODE = "fellow-e2e-code"
FIXTURES = {
    "fellow.e2e@example.com": (UserRole.FELLOW, "JBSWY3DPEHPK3PXP", True),
    "mentor.e2e@example.com": (UserRole.MENTOR, "KRSXG5DSNFXGOIDB", True),
    "admin.e2e@example.com": (UserRole.ADMIN, None, False),
    "superadmin.e2e@example.com": (UserRole.SUPER_ADMIN, None, False),
    "manager.e2e@example.com": (UserRole.PROGRAM_MANAGER, None, False),
}


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        fixture_users = db.scalars(
            select(User).where(User.email.in_(list(FIXTURES)))
        ).all()
        for user in fixture_users:
            db.delete(user)
        db.execute(delete(AuthRateLimitBucket))
        db.flush()

        for email, (role, secret, enabled) in FIXTURES.items():
            user = User(
                first_name=role.value.replace("_", " ").title(),
                last_name="E2E",
                email=email,
                password_hash=hash_password(PASSWORD),
                role=role,
                account_status=AccountStatus.ACTIVE,
                is_active=True,
                two_factor_enabled=enabled,
                totp_secret=secret,
                password_set_at=datetime.now(timezone.utc),
                email_verified_at=datetime.now(timezone.utc),
            )
            db.add(user)
            db.flush()
            if role == UserRole.FELLOW:
                db.add(
                    UserRecoveryCode(
                        user_id=user.id,
                        code_hash=hashlib.sha256(RECOVERY_CODE.encode()).hexdigest(),
                        used=False,
                    )
                )
        db.commit()


def user_for_email(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or email.lower() not in FIXTURES:
        raise RuntimeError("Unknown E2E fixture account.")
    return user


def print_totp(email: str) -> None:
    with SessionLocal() as db:
        user = user_for_email(db, email)
        if not user.totp_secret:
            raise RuntimeError("MFA setup has not created a pending secret.")
        print(pyotp.TOTP(user.totp_secret).now())


def set_session_time(email: str, mode: str) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        user = user_for_email(db, email)
        session = db.scalar(
            select(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.created_at.desc())
        )
        if session is None:
            raise RuntimeError("No active E2E session exists for this account.")
        if mode == "warning":
            session.last_activity_at = now - timedelta(hours=23, minutes=56)
        elif mode == "inactivity-expired":
            session.last_activity_at = now - timedelta(hours=24, minutes=1)
        elif mode == "absolute-expired":
            session.absolute_expires_at = now - timedelta(seconds=1)
        elif mode == "active-30m":
            session.last_activity_at = now - timedelta(minutes=30)
        elif mode == "active-60m":
            session.last_activity_at = now - timedelta(minutes=60)
        else:
            raise RuntimeError("Unsupported E2E clock mode.")
        db.commit()


def set_account_status(email: str, status: str) -> None:
    with SessionLocal() as db:
        user = user_for_email(db, email)
        user.account_status = AccountStatus(status)
        user.is_active = status != AccountStatus.SUSPENDED.value
        db.commit()


def set_password_reset_code(email: str, request_id: str) -> None:
    with SessionLocal() as db:
        user = user_for_email(db, email)
        recovery = db.get(PasswordRecoveryRequest, request_id)
        if recovery is None or recovery.user_id != user.id:
            raise RuntimeError("Password recovery request does not match fixture user.")
        recovery.code_hash = hash_recovery_code(RESET_CODE)
        recovery.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        recovery.attempt_count = 0
        db.commit()


def session_summary(email: str) -> None:
    with SessionLocal() as db:
        user = user_for_email(db, email)
        session = db.scalar(
            select(AuthSession)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.created_at.desc())
        )
        if session is None:
            print("none")
        else:
            print(f"{session.refresh_generation}:{'revoked' if session.revoked_at else 'active'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    totp_parser = sub.add_parser("totp")
    totp_parser.add_argument("email")
    time_parser = sub.add_parser("set-session-time")
    time_parser.add_argument("email")
    time_parser.add_argument(
        "mode",
        choices=["warning", "inactivity-expired", "absolute-expired", "active-30m", "active-60m"],
    )
    status_parser = sub.add_parser("set-account-status")
    status_parser.add_argument("email")
    status_parser.add_argument("status", choices=["active", "suspended"])
    reset_parser = sub.add_parser("set-password-reset-code")
    reset_parser.add_argument("email")
    reset_parser.add_argument("request_id")
    summary_parser = sub.add_parser("session-summary")
    summary_parser.add_argument("email")
    args = parser.parse_args()

    if args.command == "seed":
        seed()
    elif args.command == "totp":
        print_totp(args.email)
    elif args.command == "set-session-time":
        set_session_time(args.email, args.mode)
    elif args.command == "set-account-status":
        set_account_status(args.email, args.status)
    elif args.command == "set-password-reset-code":
        set_password_reset_code(args.email, args.request_id)
    elif args.command == "session-summary":
        session_summary(args.email)


if __name__ == "__main__":
    main()