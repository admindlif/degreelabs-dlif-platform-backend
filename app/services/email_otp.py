import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email_otp import EmailOTPChallenge
from app.models.user import User, UserRole
from app.repositories.email_otp import (
    count_recent_otp_requests_for_user,
    create_otp_challenge,
    get_latest_active_challenge_for_user,
    get_latest_challenge_for_user,
    invalidate_pending_challenges,
)
from app.services.email import send_email

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5
MAX_OTP_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
HOURLY_RATE_LIMIT = 5

PRIVILEGED_ROLES = {
    UserRole.ADMIN,
    UserRole.SUPER_ADMIN,
    UserRole.PROGRAM_MANAGER,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EmailOTPError(Exception):
    """Base exception for Email OTP operations."""


class EmailOTPForbiddenError(EmailOTPError):
    """Raised when an administrative or privileged role attempts Email OTP."""


class EmailOTPRateLimitError(EmailOTPError):
    """Raised when hourly rate limit is exceeded."""


class EmailOTPCooldownError(EmailOTPError):
    """Raised when requesting a new code before the resend cooldown expires."""


class EmailOTPExpiredError(EmailOTPError):
    """Raised when a code has expired."""


class EmailOTPAttemptLimitError(EmailOTPError):
    """Raised when max verification attempts have been reached."""


class InvalidEmailOTPCodeError(EmailOTPError):
    """Raised when the submitted OTP code does not match."""


# ---------------------------------------------------------------------------
# Cryptographic & Formatting Utilities
# ---------------------------------------------------------------------------


def mask_email(email: str) -> str:
    """Mask email for safe client presentation (e.g. s***@gmail.com)."""
    if not email or "@" not in email:
        return "***"
    local_part, domain = email.strip().split("@", 1)
    if len(local_part) <= 1:
        masked_local = f"{local_part}***"
    else:
        masked_local = f"{local_part[0]}***"
    return f"{masked_local}@{domain}"


def generate_otp_code() -> str:
    """Generate a cryptographically secure 6-digit numeric verification code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(user_id: UUID | str, purpose: str, code: str) -> str:
    """
    Generate an HMAC-SHA256 hex digest of the 6-digit code.

    Binds the hash cryptographically to user_id and purpose to prevent
    token reuse across users or between activation and login contexts.
    """
    message = f"{str(user_id)}:{purpose.strip().lower()}:{code.strip()}"
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_otp_code(user_id: UUID | str, purpose: str, code: str, expected_hash: str) -> bool:
    """Timing-safe verification of the OTP code hash."""
    computed_hash = hash_otp_code(user_id, purpose, code)
    return hmac.compare_digest(computed_hash, expected_hash)


def _format_otp_email(code: str, user_name: str, purpose: str) -> tuple[str, str]:
    """Build accessible, responsive HTML and plain-text email bodies for OTP."""
    action_description = "sign in to your account" if purpose == "login" else "activate your account"

    text_body = (
        f"Hello {user_name},\n\n"
        f"Your verification code to {action_description} is:\n\n"
        f"    {code}\n\n"
        f"This code will expire in {OTP_EXPIRY_MINUTES} minutes.\n\n"
        "Security notice: Never share this code with anyone. DegreeLabs staff will never "
        "ask for your verification code.\n\n"
        "If you did not request this verification code, please ignore this email.\n\n"
        "— The DegreeLabs Team"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Your Verification Code</title>
</head>
<body style="margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #0f172a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 520px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden;">
    <tr>
      <td style="padding: 32px 32px 24px; text-align: left;">
        <h2 style="margin: 0 0 16px; font-size: 20px; font-weight: 600; color: #0f172a;">DegreeLabs Verification</h2>
        <p style="margin: 0 0 16px; font-size: 15px; line-height: 24px; color: #334155;">Hello {user_name},</p>
        <p style="margin: 0 0 24px; font-size: 15px; line-height: 24px; color: #334155;">Use the following verification code to {action_description}:</p>
        <div style="background-color: #f1f5f9; border-radius: 6px; padding: 18px 24px; text-align: center; margin: 0 0 24px; letter-spacing: 6px; font-size: 32px; font-weight: 700; color: #0284c7; font-family: monospace;">
          {code}
        </div>
        <p style="margin: 0 0 12px; font-size: 14px; line-height: 20px; color: #64748b;">
          This code will expire in <strong>{OTP_EXPIRY_MINUTES} minutes</strong>.
        </p>
        <p style="margin: 0 0 20px; font-size: 13px; line-height: 18px; color: #64748b;">
          <strong>Security notice:</strong> Never share this code with anyone. DegreeLabs staff will never ask for your verification code.
        </p>
        <p style="margin: 0; font-size: 13px; line-height: 18px; color: #94a3b8;">
          If you did not request this verification code, please disregard this email.
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding: 16px 32px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8; text-align: center;">
        &copy; DegreeLabs. All rights reserved.
      </td>
    </tr>
  </table>
</body>
</html>"""
    return html_body, text_body


# ---------------------------------------------------------------------------
# Core Service Logic
# ---------------------------------------------------------------------------


def send_email_otp(
    db: Session,
    user: User,
    purpose: str,
    ip_address: str | None = None,
) -> tuple[EmailOTPChallenge, str]:
    """
    Generate, hash, store, and deliver a 6-digit Email OTP to the user's email address.

    Enforces:
    - Exclusion of privileged roles (Admin, Super Admin, Program Manager)
    - 60-second cooldown between resends
    - 5 requests per hour rate limit
    - 5-minute expiration
    - Single-use challenge lifecycle
    """
    if user.role in PRIVILEGED_ROLES:
        logger.warning(
            "Privileged user attempted Email OTP: user_id=%s role=%s",
            str(user.id),
            user.role.value,
        )
        raise EmailOTPForbiddenError(
            "Email OTP is not permitted for privileged administrative roles."
        )

    now = datetime.now(timezone.utc)

    # 1. Check hourly rate limit
    one_hour_ago = now - timedelta(hours=1)
    recent_count = count_recent_otp_requests_for_user(db, user.id, one_hour_ago)
    if recent_count >= HOURLY_RATE_LIMIT:
        raise EmailOTPRateLimitError(
            "Too many verification requests. Please try again later."
        )

    # 2. Check 60-second cooldown against the latest challenge
    latest_challenge = get_latest_challenge_for_user(db, user.id, purpose)
    if latest_challenge is not None and latest_challenge.last_sent_at is not None:
        last_sent = latest_challenge.last_sent_at
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        elapsed_seconds = (now - last_sent).total_seconds()
        if elapsed_seconds < RESEND_COOLDOWN_SECONDS:
            remaining_seconds = int(RESEND_COOLDOWN_SECONDS - elapsed_seconds)
            raise EmailOTPCooldownError(
                f"Please wait {remaining_seconds} second(s) before requesting a new code."
            )

    # 3. Generate 6-digit numeric code
    code = generate_otp_code()
    resend_count = 0
    if latest_challenge is not None and latest_challenge.consumed_at is None:
        resend_count = latest_challenge.resend_count + 1

    # 4. Invalidate pending challenges for this user and purpose
    invalidate_pending_challenges(db, user.id, purpose)

    # 5. Format email and attempt delivery before persisting or committing
    html_body, text_body = _format_otp_email(
        code=code,
        user_name=user.first_name,
        purpose=purpose,
    )

    try:
        send_email(
            to_address=user.email,
            subject="DegreeLabs — Your Verification Code",
            html_body=html_body,
            text_body=text_body,
        )
    except Exception:
        logger.exception("Failed to deliver Email OTP to user_id=%s", str(user.id))
        raise

    # 6. Save challenge record
    challenge = EmailOTPChallenge(
        user_id=user.id,
        email=user.email,
        purpose=purpose,
        code_hash=hash_otp_code(user.id, purpose, code),
        expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
        attempt_count=0,
        resend_count=resend_count,
        last_sent_at=now,
        created_at=now,
        ip_address=ip_address,
    )
    create_otp_challenge(db, challenge)
    db.commit()
    db.refresh(challenge)

    logger.info(
        "Email OTP challenge issued: user_id=%s purpose=%s expires_at=%s",
        str(user.id),
        purpose,
        challenge.expires_at.isoformat(),
    )
    return challenge, code


def verify_email_otp(
    db: Session,
    user: User,
    purpose: str,
    code: str,
) -> EmailOTPChallenge:
    """
    Verify the 6-digit Email OTP code for the user and purpose.

    Enforces:
    - Exclusion of privileged roles
    - Code expiry (5 minutes)
    - Max 5 failed attempts per challenge
    - Single-use consumption
    """
    if user.role in PRIVILEGED_ROLES:
        raise EmailOTPForbiddenError(
            "Email OTP is not permitted for privileged administrative roles."
        )

    challenge = get_latest_active_challenge_for_user(db, user.id, purpose)
    if challenge is None:
        raise InvalidEmailOTPCodeError(
            "No active verification code found. Please request a new code."
        )

    now = datetime.now(timezone.utc)

    # Check expiration
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise EmailOTPExpiredError(
            "Verification code has expired. Please request a new code."
        )

    # Check attempt limit
    if challenge.attempt_count >= MAX_OTP_ATTEMPTS:
        raise EmailOTPAttemptLimitError(
            "Too many incorrect attempts. This code has been invalidated. Please request a new code."
        )

    # Timing-safe comparison
    if not verify_otp_code(user.id, purpose, code, challenge.code_hash):
        challenge.attempt_count += 1
        db.commit()
        remaining_attempts = max(0, MAX_OTP_ATTEMPTS - challenge.attempt_count)
        if remaining_attempts == 0:
            raise EmailOTPAttemptLimitError(
                "Too many incorrect attempts. This code has been invalidated. Please request a new code."
            )
        raise InvalidEmailOTPCodeError(
            f"Invalid verification code. {remaining_attempts} attempt(s) remaining."
        )

    # Code is valid -> consume it
    challenge.consumed_at = now
    db.commit()
    db.refresh(challenge)

    logger.info(
        "Email OTP successfully verified: user_id=%s purpose=%s challenge_id=%s",
        str(user.id),
        purpose,
        str(challenge.id),
    )
    return challenge

