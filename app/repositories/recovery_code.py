"""
Repository for UserRecoveryCode.

Handles all database operations for 2FA recovery codes.
Plain-text codes must never reach this layer.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_recovery_code import UserRecoveryCode


def get_unused_recovery_code_by_hash(
    db: Session,
    user_id: UUID,
    code_hash: str,
) -> UserRecoveryCode | None:
    """
    Return an unused recovery code matching the given hash for the user.

    Returns ``None`` if no matching unused code exists.
    """
    statement = select(UserRecoveryCode).where(
        UserRecoveryCode.user_id == user_id,
        UserRecoveryCode.code_hash == code_hash,
        UserRecoveryCode.used.is_(False),
    )
    return db.scalar(statement)


def create_recovery_codes(
    db: Session,
    codes: list[UserRecoveryCode],
) -> list[UserRecoveryCode]:
    """Persist a batch of recovery code records."""
    db.add_all(codes)
    db.flush()
    return codes


def delete_all_recovery_codes_for_user(
    db: Session,
    user_id: UUID,
) -> None:
    """
    Remove all existing recovery codes for a user.

    Used when regenerating codes or resetting 2FA.
    """
    statement = select(UserRecoveryCode).where(
        UserRecoveryCode.user_id == user_id
    )
    codes = db.scalars(statement).all()
    for code in codes:
        db.delete(code)
    db.flush()
