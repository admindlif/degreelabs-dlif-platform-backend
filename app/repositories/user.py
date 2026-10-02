"""
Repository for the User model.

Handles all database read/write operations for users.
Business logic belongs in services/auth.py.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_user_by_email(
    db: Session,
    email: str,
) -> User | None:
    """Look up a user by their normalized (lowercase) email address."""
    statement = select(User).where(
        User.email == email.lower()
    )
    return db.scalar(statement)


def get_user_by_id(
    db: Session,
    user_id: UUID,
) -> User | None:
    """Look up a user by their UUID primary key."""
    return db.get(User, user_id)


def create_user(
    db: Session,
    user: User,
) -> User:
    """
    Add a new user to the session.

    Uses ``flush()`` rather than ``commit()`` so callers can compose
    multiple writes within a single transaction.  The caller must call
    ``db.commit()`` after all related writes succeed.
    """
    db.add(user)
    db.flush()
    return user