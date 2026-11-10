from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.email_otp import EmailOTPChallenge


def create_otp_challenge(
    db: Session,
    challenge: EmailOTPChallenge,
) -> EmailOTPChallenge:
    db.add(challenge)
    db.flush()
    return challenge


def get_latest_active_challenge_for_user(
    db: Session,
    user_id: UUID,
    purpose: str,
) -> EmailOTPChallenge | None:
    stmt = (
        select(EmailOTPChallenge)
        .where(
            EmailOTPChallenge.user_id == user_id,
            EmailOTPChallenge.purpose == purpose,
            EmailOTPChallenge.consumed_at.is_(None),
        )
        .order_by(EmailOTPChallenge.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def get_latest_challenge_for_user(
    db: Session,
    user_id: UUID,
    purpose: str,
) -> EmailOTPChallenge | None:
    stmt = (
        select(EmailOTPChallenge)
        .where(
            EmailOTPChallenge.user_id == user_id,
            EmailOTPChallenge.purpose == purpose,
        )
        .order_by(EmailOTPChallenge.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def count_recent_otp_requests_for_user(
    db: Session,
    user_id: UUID,
    since: datetime,
) -> int:
    stmt = (
        select(func.count(EmailOTPChallenge.id))
        .where(
            EmailOTPChallenge.user_id == user_id,
            EmailOTPChallenge.created_at >= since,
        )
    )
    return db.execute(stmt).scalar() or 0


def invalidate_pending_challenges(
    db: Session,
    user_id: UUID,
    purpose: str,
) -> None:
    stmt = (
        update(EmailOTPChallenge)
        .where(
            EmailOTPChallenge.user_id == user_id,
            EmailOTPChallenge.purpose == purpose,
            EmailOTPChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=func.now())
    )
    db.execute(stmt)
    db.flush()

