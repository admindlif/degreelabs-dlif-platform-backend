"""
Repository for PasswordRecoveryRequest.

Handles database operations for self-service password recovery.
Plain-text verification codes and reset tokens must NEVER reach this layer.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.password_recovery import PasswordRecoveryRequest


def create_recovery_request(
    db: Session,
    request: PasswordRecoveryRequest,
) -> PasswordRecoveryRequest:
    """Persist a new password recovery request."""
    db.add(request)
    db.flush()
    return request


def get_recovery_request_by_id(
    db: Session,
    request_id: UUID,
) -> PasswordRecoveryRequest | None:
    """Lookup a recovery request by primary key."""
    statement = select(PasswordRecoveryRequest).where(
        PasswordRecoveryRequest.id == request_id
    )
    return db.scalar(statement)


def get_recovery_request_by_reset_token_hash(
    db: Session,
    token_hash: str,
) -> PasswordRecoveryRequest | None:
    """Lookup a recovery request by its hashed reset authorization token."""
    statement = select(PasswordRecoveryRequest).where(
        PasswordRecoveryRequest.reset_token_hash == token_hash,
        PasswordRecoveryRequest.consumed_at.is_(None),
    )
    return db.scalar(statement)


def get_latest_active_request_for_user(
    db: Session,
    user_id: UUID,
) -> PasswordRecoveryRequest | None:
    """
    Get the most recent unconsumed recovery request for a user.
    """
    statement = (
        select(PasswordRecoveryRequest)
        .where(
            PasswordRecoveryRequest.user_id == user_id,
            PasswordRecoveryRequest.consumed_at.is_(None),
        )
        .order_by(PasswordRecoveryRequest.created_at.desc())
        .limit(1)
    )
    return db.scalar(statement)


def count_recent_requests_for_user(
    db: Session,
    user_id: UUID,
    since: datetime,
) -> int:
    """Count how many requests have been created for this user since a timestamp."""
    statement = select(func.count(PasswordRecoveryRequest.id)).where(
        PasswordRecoveryRequest.user_id == user_id,
        PasswordRecoveryRequest.created_at >= since,
    )
    return db.scalar(statement) or 0


def invalidate_active_requests_for_user(
    db: Session,
    user_id: UUID,
) -> None:
    """Invalidate all unconsumed recovery requests for a user upon reset or new code generation."""
    now = datetime.now(timezone.utc)
    statement = select(PasswordRecoveryRequest).where(
        PasswordRecoveryRequest.user_id == user_id,
        PasswordRecoveryRequest.consumed_at.is_(None),
    )
    requests = db.scalars(statement).all()
    for req in requests:
        req.consumed_at = now
    db.flush()
