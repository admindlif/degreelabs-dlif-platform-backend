"""Server-managed authentication sessions and refresh-token rotation.

Raw refresh tokens exist only in memory long enough to be set in an HttpOnly
cookie. PostgreSQL stores SHA-256 hashes and an append-only rotation history.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.models.auth_session import (
    AuthAuditEvent,
    AuthSession,
    AuthSessionRefreshToken,
)
from app.models.user import AccountStatus, User, UserRole


class AuthSessionError(Exception):
    """Base class for authenticated-session failures."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class InvalidRefreshTokenError(AuthSessionError):
    pass


class SessionExpiredError(AuthSessionError):
    pass


class SessionRevokedError(AuthSessionError):
    pass


class RefreshTokenReuseError(AuthSessionError):
    pass


class ConcurrentRefreshError(AuthSessionError):
    """The same token was rotated by a concurrent request moments earlier."""


@dataclass(frozen=True)
class RequestMetadata:
    ip_address_hash: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class IssuedSessionTokens:
    session: AuthSession
    access_token: str
    refresh_token: str
    refresh_cookie_max_age: int


@dataclass(frozen=True)
class SessionDeadlines:
    inactivity_expires_at: datetime
    absolute_expires_at: datetime

    @property
    def effective_expires_at(self) -> datetime:
        return min(self.inactivity_expires_at, self.absolute_expires_at)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_client_identifier(value: str | None) -> str | None:
    """Return a keyed, non-reversible identifier for IP/rate-limit metadata."""
    if not value:
        return None
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        value.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sanitize_user_agent(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\r", " ").replace("\n", " ")[:512]


def absolute_lifetime_for_role(role: UserRole) -> timedelta:
    if role in {UserRole.FELLOW, UserRole.STUDENT}:
        hours = settings.session_absolute_fellow_hours
    elif role == UserRole.MENTOR:
        hours = settings.session_absolute_mentor_hours
    else:
        hours = settings.session_absolute_privileged_hours
    return timedelta(hours=hours)


def session_deadlines(session: AuthSession) -> SessionDeadlines:
    return SessionDeadlines(
        inactivity_expires_at=(
            _as_utc(session.last_activity_at)
            + timedelta(minutes=settings.session_inactivity_timeout_minutes)
        ),
        absolute_expires_at=_as_utc(session.absolute_expires_at),
    )


def add_audit_event(
    db: Session,
    event_type: str,
    *,
    user_id: UUID | None = None,
    session_id: UUID | None = None,
    success: bool = True,
    reason: str | None = None,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> AuthAuditEvent:
    event = AuthAuditEvent(
        user_id=user_id,
        session_id=session_id,
        event_type=event_type[:64],
        success=success,
        reason=reason[:160] if reason else None,
        ip_address_hash=metadata.ip_address_hash if metadata else None,
        user_agent=metadata.user_agent if metadata else None,
        occurred_at=now or utc_now(),
    )
    db.add(event)
    return event


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(settings.session_refresh_token_bytes)


def _refresh_expiry(now: datetime, absolute_expiry: datetime) -> datetime:
    return min(
        now + timedelta(hours=settings.session_refresh_token_expire_hours),
        absolute_expiry,
    )


def _refresh_cookie_max_age(expires_at: datetime, now: datetime) -> int:
    return max(0, int((_as_utc(expires_at) - now).total_seconds()))


def create_authenticated_session(
    db: Session,
    user: User,
    *,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> IssuedSessionTokens:
    """Create a server session after password and required MFA verification."""
    issued_at = now or utc_now()
    absolute_expires_at = issued_at + absolute_lifetime_for_role(user.role)
    refresh_expires_at = _refresh_expiry(issued_at, absolute_expires_at)
    raw_refresh_token = _new_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)

    session = AuthSession(
        user_id=user.id,
        current_refresh_token_hash=token_hash,
        refresh_generation=0,
        created_at=issued_at,
        last_activity_at=issued_at,
        refresh_token_expires_at=refresh_expires_at,
        absolute_expires_at=absolute_expires_at,
        user_agent=metadata.user_agent if metadata else None,
        ip_address_hash=metadata.ip_address_hash if metadata else None,
    )
    db.add(session)
    db.flush()

    db.add(
        AuthSessionRefreshToken(
            session_id=session.id,
            token_hash=token_hash,
            generation=0,
            issued_at=issued_at,
            expires_at=refresh_expires_at,
        )
    )
    user.last_login_at = issued_at
    add_audit_event(
        db,
        "login_success",
        user_id=user.id,
        session_id=session.id,
        metadata=metadata,
        now=issued_at,
    )
    db.commit()
    db.refresh(session)

    access_token = create_access_token(
        str(user.id),
        role=user.role,
        session_id=session.id,
        now=issued_at,
    )
    return IssuedSessionTokens(
        session=session,
        access_token=access_token,
        refresh_token=raw_refresh_token,
        refresh_cookie_max_age=_refresh_cookie_max_age(
            refresh_expires_at,
            issued_at,
        ),
    )


def revoke_session(
    db: Session,
    session: AuthSession,
    *,
    reason: str,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
    audit_event_type: str = "session_revoked",
) -> bool:
    if session.revoked_at is not None:
        return False
    revoked_at = now or utc_now()
    session.revoked_at = revoked_at
    session.revoked_reason = reason[:100]
    add_audit_event(
        db,
        audit_event_type,
        user_id=session.user_id,
        session_id=session.id,
        reason=reason,
        metadata=metadata,
        now=revoked_at,
    )
    return True


def revoke_all_user_sessions(
    db: Session,
    user_id: UUID,
    *,
    reason: str,
    now: datetime | None = None,
) -> int:
    revoked_at = now or utc_now()
    sessions = list(
        db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
        )
    )
    for session in sessions:
        revoke_session(
            db,
            session,
            reason=reason,
            now=revoked_at,
        )
    return len(sessions)


def _persist_expiration(
    db: Session,
    session: AuthSession,
    *,
    reason: str,
    metadata: RequestMetadata | None,
    now: datetime,
) -> None:
    revoke_session(
        db,
        session,
        reason=reason,
        metadata=metadata,
        now=now,
        audit_event_type="session_expired",
    )
    db.commit()


def ensure_session_active(
    db: Session,
    session: AuthSession,
    user: User,
    *,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> SessionDeadlines:
    checked_at = now or utc_now()

    if session.revoked_at is not None:
        raise SessionRevokedError(
            "Authentication session has been revoked.",
            reason=session.revoked_reason or "revoked",
        )

    if not user.is_active or user.account_status == AccountStatus.SUSPENDED:
        _persist_expiration(
            db,
            session,
            reason="account_suspended",
            metadata=metadata,
            now=checked_at,
        )
        raise SessionRevokedError(
            "Account is inactive or suspended.",
            reason="account_suspended",
        )

    if user.account_status != AccountStatus.ACTIVE:
        _persist_expiration(
            db,
            session,
            reason="account_not_active",
            metadata=metadata,
            now=checked_at,
        )
        raise SessionRevokedError(
            "Account setup is incomplete.",
            reason="account_not_active",
        )

    deadlines = session_deadlines(session)
    if checked_at >= deadlines.absolute_expires_at:
        _persist_expiration(
            db,
            session,
            reason="absolute_expired",
            metadata=metadata,
            now=checked_at,
        )
        raise SessionExpiredError(
            "The maximum session lifetime has ended.",
            reason="absolute_expired",
        )

    if checked_at >= deadlines.inactivity_expires_at:
        _persist_expiration(
            db,
            session,
            reason="inactivity_expired",
            metadata=metadata,
            now=checked_at,
        )
        raise SessionExpiredError(
            "The session expired due to inactivity.",
            reason="inactivity_expired",
        )

    return deadlines


def get_active_session_for_access(
    db: Session,
    session_id: UUID,
    user: User,
    *,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> AuthSession:
    session = db.get(AuthSession, session_id)
    if session is None or session.user_id != user.id:
        raise SessionRevokedError(
            "Authentication session was not found.",
            reason="session_not_found",
        )
    ensure_session_active(
        db,
        session,
        user,
        metadata=metadata,
        now=now,
    )
    return session


def refresh_authenticated_session(
    db: Session,
    raw_refresh_token: str,
    *,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> IssuedSessionTokens:
    """Rotate a refresh token using row locks and a bounded race window."""
    checked_at = now or utc_now()
    token_hash = hash_refresh_token(raw_refresh_token)

    refresh_record = db.scalar(
        select(AuthSessionRefreshToken)
        .where(AuthSessionRefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    if refresh_record is None:
        add_audit_event(
            db,
            "refresh_failure",
            success=False,
            reason="unknown_refresh_token",
            metadata=metadata,
            now=checked_at,
        )
        db.commit()
        raise InvalidRefreshTokenError(
            "Invalid refresh token.",
            reason="invalid_refresh_token",
        )

    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == refresh_record.session_id)
        .with_for_update()
    )
    if session is None:
        raise InvalidRefreshTokenError(
            "Authentication session was not found.",
            reason="session_not_found",
        )

    user = db.get(User, session.user_id)
    if user is None:
        revoke_session(
            db,
            session,
            reason="user_not_found",
            metadata=metadata,
            now=checked_at,
        )
        db.commit()
        raise SessionRevokedError(
            "Authentication session is no longer valid.",
            reason="user_not_found",
        )

    if refresh_record.consumed_at is not None:
        consumed_at = _as_utc(refresh_record.consumed_at)
        grace_deadline = consumed_at + timedelta(
            seconds=settings.session_refresh_race_grace_seconds
        )
        if checked_at <= grace_deadline:
            # The winning request will set the shared API-host cookie. Do not
            # overwrite it and do not revoke the session for this expected race.
            db.rollback()
            raise ConcurrentRefreshError(
                "A concurrent request already refreshed this session.",
                reason="concurrent_refresh",
            )

        refresh_record.reuse_detected_at = checked_at
        revoke_session(
            db,
            session,
            reason="refresh_token_reuse",
            metadata=metadata,
            now=checked_at,
            audit_event_type="refresh_token_reuse",
        )
        db.commit()
        raise RefreshTokenReuseError(
            "Refresh token reuse was detected and the session was revoked.",
            reason="refresh_token_reuse",
        )

    if token_hash != session.current_refresh_token_hash:
        refresh_record.reuse_detected_at = checked_at
        revoke_session(
            db,
            session,
            reason="refresh_token_not_current",
            metadata=metadata,
            now=checked_at,
            audit_event_type="refresh_token_reuse",
        )
        db.commit()
        raise RefreshTokenReuseError(
            "A stale refresh token was presented.",
            reason="refresh_token_not_current",
        )

    ensure_session_active(
        db,
        session,
        user,
        metadata=metadata,
        now=checked_at,
    )

    if checked_at >= _as_utc(refresh_record.expires_at):
        revoke_session(
            db,
            session,
            reason="refresh_token_expired",
            metadata=metadata,
            now=checked_at,
        )
        db.commit()
        raise SessionExpiredError(
            "Refresh token has expired.",
            reason="refresh_token_expired",
        )

    new_raw_token = _new_refresh_token()
    new_token_hash = hash_refresh_token(new_raw_token)
    new_generation = session.refresh_generation + 1
    new_expiry = _refresh_expiry(
        checked_at,
        _as_utc(session.absolute_expires_at),
    )
    replacement = AuthSessionRefreshToken(
        session_id=session.id,
        token_hash=new_token_hash,
        generation=new_generation,
        issued_at=checked_at,
        expires_at=new_expiry,
    )
    db.add(replacement)
    db.flush()

    refresh_record.consumed_at = checked_at
    refresh_record.replaced_by_token_id = replacement.id
    session.current_refresh_token_hash = new_token_hash
    session.refresh_generation = new_generation
    session.refresh_token_expires_at = new_expiry
    add_audit_event(
        db,
        "refresh_success",
        user_id=user.id,
        session_id=session.id,
        metadata=metadata,
        now=checked_at,
    )
    db.commit()
    db.refresh(session)

    return IssuedSessionTokens(
        session=session,
        access_token=create_access_token(
            str(user.id),
            role=user.role,
            session_id=session.id,
            now=checked_at,
        ),
        refresh_token=new_raw_token,
        refresh_cookie_max_age=_refresh_cookie_max_age(new_expiry, checked_at),
    )


def get_session_from_refresh_token(
    db: Session,
    raw_refresh_token: str,
    *,
    now: datetime | None = None,
) -> AuthSession | None:
    checked_at = now or utc_now()
    presented_hash = hash_refresh_token(raw_refresh_token)
    refresh_record = db.scalar(
        select(AuthSessionRefreshToken).where(
            AuthSessionRefreshToken.token_hash
            == presented_hash
        )
    )
    if refresh_record is None:
        return None
    session = db.get(AuthSession, refresh_record.session_id)
    if session is None:
        return None
    if presented_hash == session.current_refresh_token_hash:
        return session
    if refresh_record.consumed_at is not None:
        grace_deadline = _as_utc(refresh_record.consumed_at) + timedelta(
            seconds=settings.session_refresh_race_grace_seconds
        )
        if checked_at <= grace_deadline:
            return session
    return None


def get_session_from_access_token(
    db: Session,
    access_token: str,
) -> AuthSession | None:
    try:
        payload = decode_access_token(access_token, verify_expiration=False)
        session_id = UUID(payload["sid"])
    except Exception:
        return None
    return db.get(AuthSession, session_id)


def record_genuine_activity(
    db: Session,
    session: AuthSession,
    user: User,
    *,
    metadata: RequestMetadata | None = None,
    now: datetime | None = None,
) -> SessionDeadlines:
    activity_at = now or utc_now()
    ensure_session_active(
        db,
        session,
        user,
        metadata=metadata,
        now=activity_at,
    )

    last_activity = _as_utc(session.last_activity_at)
    if (
        activity_at - last_activity
        >= timedelta(seconds=settings.session_activity_write_interval_seconds)
    ):
        session.last_activity_at = activity_at
        db.commit()
        db.refresh(session)

    return session_deadlines(session)
