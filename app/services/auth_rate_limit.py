"""Database-backed authentication throttling shared by all API workers."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth_session import AuthRateLimitBucket
from app.services.auth_session import hash_client_identifier


class AuthRateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many authentication attempts. Please try again later.")
        self.retry_after_seconds = max(1, retry_after_seconds)


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window: timedelta


def login_policy() -> RateLimitPolicy:
    return RateLimitPolicy(
        settings.auth_login_attempt_limit,
        timedelta(minutes=settings.auth_login_window_minutes),
    )


def two_fa_policy() -> RateLimitPolicy:
    return RateLimitPolicy(
        settings.auth_two_fa_attempt_limit,
        timedelta(minutes=settings.auth_two_fa_window_minutes),
    )


def recovery_policy() -> RateLimitPolicy:
    return RateLimitPolicy(
        settings.auth_recovery_attempt_limit,
        timedelta(minutes=settings.auth_recovery_window_minutes),
    )


def refresh_policy() -> RateLimitPolicy:
    return RateLimitPolicy(
        settings.auth_refresh_attempt_limit,
        timedelta(minutes=settings.auth_refresh_window_minutes),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _key_hash(identifier: str) -> str:
    value = hash_client_identifier(identifier)
    if value is None:
        raise ValueError("Rate-limit identifiers must not be blank.")
    return value


def _active_block_retry_after(
    bucket: AuthRateLimitBucket,
    *,
    now: datetime,
) -> int | None:
    if bucket.blocked_until is None:
        return None
    blocked_until = _utc(bucket.blocked_until)
    if blocked_until <= now:
        return None
    return int((blocked_until - now).total_seconds()) + 1


def enforce_rate_limit(
    db: Session,
    action: str,
    identifiers: list[str],
    *,
    policy: RateLimitPolicy,
    now: datetime | None = None,
) -> None:
    checked_at = now or datetime.now(timezone.utc)
    key_hashes = [_key_hash(value) for value in identifiers if value]
    if not key_hashes:
        return

    buckets = list(
        db.scalars(
            select(AuthRateLimitBucket).where(
                AuthRateLimitBucket.action == action,
                AuthRateLimitBucket.key_hash.in_(key_hashes),
            )
        )
    )
    retry_after = 0
    for bucket in buckets:
        retry_after = max(
            retry_after,
            _active_block_retry_after(bucket, now=checked_at) or 0,
        )
    if retry_after:
        raise AuthRateLimitExceeded(retry_after)


def register_rate_limit_failure(
    db: Session,
    action: str,
    identifiers: list[str],
    *,
    policy: RateLimitPolicy,
    now: datetime | None = None,
) -> None:
    failed_at = now or datetime.now(timezone.utc)
    key_hashes = [_key_hash(value) for value in identifiers if value]

    for key_hash in key_hashes:
        db.execute(
            insert(AuthRateLimitBucket)
            .values(
                id=uuid4(),
                action=action,
                key_hash=key_hash,
                window_started_at=failed_at,
                attempt_count=0,
                updated_at=failed_at,
            )
            .on_conflict_do_nothing(
                index_elements=["action", "key_hash"],
            )
        )
        bucket = db.scalar(
            select(AuthRateLimitBucket)
            .where(
                AuthRateLimitBucket.action == action,
                AuthRateLimitBucket.key_hash == key_hash,
            )
            .with_for_update()
        )
        if bucket is None:
            continue

        window_started_at = _utc(bucket.window_started_at)
        if failed_at >= window_started_at + policy.window:
            bucket.window_started_at = failed_at
            bucket.attempt_count = 0
            bucket.blocked_until = None

        bucket.attempt_count += 1
        bucket.updated_at = failed_at
        if bucket.attempt_count >= policy.limit:
            bucket.blocked_until = failed_at + policy.window

    db.commit()


def clear_rate_limit_failures(
    db: Session,
    action: str,
    identifiers: list[str],
) -> None:
    key_hashes = [_key_hash(value) for value in identifiers if value]
    if not key_hashes:
        return
    db.execute(
        delete(AuthRateLimitBucket).where(
            AuthRateLimitBucket.action == action,
            AuthRateLimitBucket.key_hash.in_(key_hashes),
        )
    )
    db.commit()
