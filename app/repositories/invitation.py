"""
Repository for UserInvitationToken.

Handles all database operations for invitation tokens.
Business logic belongs in services/invitation.py.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_invitation import UserInvitationToken


def get_invitation_by_token_hash(
    db: Session,
    token_hash: str,
) -> UserInvitationToken | None:
    """Look up an invitation token record by its SHA-256 hash."""
    statement = select(UserInvitationToken).where(
        UserInvitationToken.token_hash == token_hash
    )
    return db.scalar(statement)


def get_active_invitation_for_user(
    db: Session,
    user_id: UUID,
) -> UserInvitationToken | None:
    """
    Return the most recent unused, non-expired invitation for a user.

    Returns ``None`` if no valid invitation exists.
    """
    now = datetime.now(timezone.utc)
    statement = (
        select(UserInvitationToken)
        .where(
            UserInvitationToken.user_id == user_id,
            UserInvitationToken.used_at.is_(None),
            UserInvitationToken.expires_at > now,
        )
        .order_by(UserInvitationToken.created_at.desc())
        .limit(1)
    )
    return db.scalar(statement)


def create_invitation_token(
    db: Session,
    invitation: UserInvitationToken,
) -> UserInvitationToken:
    """Persist a new invitation token record."""
    db.add(invitation)
    db.flush()  # flush to catch constraint errors before committing
    return invitation


def mark_invitation_used(
    db: Session,
    invitation: UserInvitationToken,
) -> UserInvitationToken:
    """
    Mark an invitation token as consumed.

    The session must be committed by the caller (usually within
    a transaction in the service layer).
    """
    invitation.used_at = datetime.now(timezone.utc)
    db.flush()
    return invitation
