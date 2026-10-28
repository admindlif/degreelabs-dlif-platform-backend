"""
Password Recovery Service.

Implements secure self-service password recovery for Fellow and Mentor portals:
- Cryptographically secure 8-digit numeric verification code generation
- HMAC-SHA256 server-side storage with server secret
- Non-enumerating responses for email requests
- 10-minute code expiry & single-use guarantee
- Rate limiting: 60s cooldown, 5 requests/hour, 5 failed attempts/code
- Single-purpose short-lived reset authorization token
- Session invalidation upon successful password update
- Confirmation email notification after password reset
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    generate_numeric_recovery_code,
    generate_reset_authorization_token,
    hash_password,
    hash_recovery_code,
    hash_reset_token,
    verify_recovery_code,
)
from app.models.password_recovery import PasswordRecoveryRequest
from app.models.user import AccountStatus, UserRole
from app.repositories.password_recovery import (
    count_recent_requests_for_user,
    create_recovery_request,
    get_latest_active_request_for_user,
    get_recovery_request_by_id,
    get_recovery_request_by_reset_token_hash,
    invalidate_active_requests_for_user,
)
from app.repositories.user import get_user_by_email
from app.services.email import send_email

logger = logging.getLogger(__name__)

GENERIC_REQUEST_RESPONSE = (
    "If an eligible account exists for this email address, a recovery code will be sent."
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RecoveryError(Exception):
    """Base exception for recovery operations."""


class InvalidRecoveryCodeError(RecoveryError):
    pass


class RecoveryAttemptLimitError(RecoveryError):
    pass


class InvalidResetAuthorizationError(RecoveryError):
    pass


class RateLimitError(RecoveryError):
    pass


class WeakPasswordError(RecoveryError):
    pass


# ---------------------------------------------------------------------------
# Email Notifications
# ---------------------------------------------------------------------------


def _get_logo_url() -> str:
    logo_url = settings.email_logo_url
    if not logo_url:
        if settings.frontend_base_url.startswith("https://"):
            logo_url = f"{settings.frontend_base_url.rstrip('/')}/degreelabs-logo.png"
        else:
            logo_url = "https://framerusercontent.com/images/wQtZcQz0JelTmeZro2xPdEUWPwI.png"
    return logo_url


def send_password_recovery_email(
    *,
    to_address: str,
    first_name: str,
    code: str,
    portal: str,
) -> None:
    """Send the 8-digit password recovery code email."""
    subject = "DegreeLabs – Password Recovery Code"

    portal_name = "Mentor Portal" if portal == "mentor" else "Fellow Portal"

    text_body = f"""Hi {first_name},

A password reset request was received for your DegreeLabs {portal_name} account.

Your one-time recovery code is:

  {code}

This code expires in 10 minutes and can only be used once.

For your security:
- Never share this code with anyone. DegreeLabs staff will never ask for it.
- If you did not request a password reset, you can safely ignore this email. Your password will remain unchanged.

Best regards,
The DegreeLabs Team
"""

    logo_url = _get_logo_url()

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>DegreeLabs Password Recovery Code</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1a1a1a; background: #ffffff; margin: 0; padding: 0;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding: 48px 16px;">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background: #ffffff; border: 1px solid #e5e5e5; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
          <!-- Header -->
          <tr>
            <td style="background: #ffffff; padding: 32px 40px 24px; border-bottom: 1px solid #f3f4f6;">
              <img src="{logo_url}"
                   alt="DegreeLabs"
                   width="150"
                   height="32"
                   style="display: block; width: 150px; max-width: 150px; height: auto; border: 0;" />
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 40px 40px 32px;">
              <h1 style="margin: 0 0 16px; font-size: 22px; font-weight: 700; color: #0f172a;">
                Password Recovery Code
              </h1>
              <p style="margin: 0 0 24px; font-size: 15px; line-height: 1.6; color: #374151;">
                Hi {first_name},<br />
                We received a request to reset your password for your DegreeLabs {portal_name} account. Use the one-time code below to complete your recovery request:
              </p>

              <!-- Code Box -->
              <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; text-align: center; margin: 28px 0;">
                <span style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #0f172a;">
                  {code}
                </span>
              </div>

              <p style="margin: 0 0 16px; font-size: 13px; line-height: 1.5; color: #6b7280;">
                <strong>Note:</strong> This recovery code expires in <strong>10 minutes</strong> and can only be used once.
              </p>

              <div style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 12px 16px; margin: 20px 0; border-radius: 0 4px 4px 0;">
                <p style="margin: 0; font-size: 12px; color: #92400e; line-height: 1.5;">
                  <strong>Security warning:</strong> Never share this code with anyone. DegreeLabs staff will never ask for your recovery code.
                </p>
              </div>

              <p style="margin: 24px 0 0; font-size: 13px; color: #6b7280;">
                If you did not request a password reset, you can safely ignore this email. Your current password remains secure.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding: 24px 40px; border-top: 1px solid #f3f4f6; background-color: #fafafa;">
              <p style="margin: 0; font-size: 12px; color: #9ca3af;">
                DegreeLabs &middot; Secure Account Recovery
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    send_email(
        to_address=to_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_password_changed_email(
    *,
    to_address: str,
    first_name: str,
) -> None:
    """Send a confirmation notice that the account password was changed."""
    subject = "DegreeLabs – Password Changed Successfully"

    text_body = f"""Hi {first_name},

This email confirms that your DegreeLabs account password was recently changed.

If you made this change, you can safely disregard this message.

If you did not initiate this change, please contact your programme administrator immediately to protect your account.

Best regards,
The DegreeLabs Team
"""

    logo_url = _get_logo_url()

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Password Changed Successfully</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1a1a1a; background: #ffffff; margin: 0; padding: 0;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding: 48px 16px;">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background: #ffffff; border: 1px solid #e5e5e5; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
          <!-- Header -->
          <tr>
            <td style="background: #ffffff; padding: 32px 40px 24px; border-bottom: 1px solid #f3f4f6;">
              <img src="{logo_url}"
                   alt="DegreeLabs"
                   width="150"
                   height="32"
                   style="display: block; width: 150px; max-width: 150px; height: auto; border: 0;" />
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 40px 40px 32px;">
              <h1 style="margin: 0 0 16px; font-size: 22px; font-weight: 700; color: #0f172a;">
                Password Changed Successfully
              </h1>
              <p style="margin: 0 0 20px; font-size: 15px; line-height: 1.6; color: #374151;">
                Hi {first_name},<br />
                Your DegreeLabs account password has been updated successfully. You can now use your new password to sign in.
              </p>
              <div style="background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 20px 0; border-radius: 0 4px 4px 0;">
                <p style="margin: 0; font-size: 12px; color: #1e40af; line-height: 1.5;">
                  If you did not perform this password reset, please contact your programme administrator immediately.
                </p>
              </div>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding: 24px 40px; border-top: 1px solid #f3f4f6; background-color: #fafafa;">
              <p style="margin: 0; font-size: 12px; color: #9ca3af;">
                DegreeLabs &middot; Security Notifications
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    send_email(
        to_address=to_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


# ---------------------------------------------------------------------------
# Recovery Workflow Operations
# ---------------------------------------------------------------------------


def request_password_recovery(
    db: Session,
    email: str,
    portal: str = "fellow",
) -> tuple[str, UUID]:
    """
    Initiate a password recovery request.

    - Returns (generic_message, recovery_request_id).
    - Always non-enumerating: invalid emails, unauthorized roles, or inactive accounts
      return an unbacked dummy UUID with identical response text.
    """
    normalized_email = email.strip().lower()
    portal_normalized = portal.strip().lower()
    dummy_id = uuid4()

    user = get_user_by_email(db, normalized_email)
    if user is None:
        logger.info("Password recovery requested for non-existent email (omitted)")
        return GENERIC_REQUEST_RESPONSE, dummy_id

    # Verify role authorization according to portal
    if portal_normalized == "fellow":
        if user.role not in {UserRole.FELLOW, UserRole.STUDENT}:
            logger.info("User role not eligible for fellow password recovery")
            return GENERIC_REQUEST_RESPONSE, dummy_id
    elif portal_normalized == "mentor":
        if user.role not in {UserRole.MENTOR, UserRole.PROGRAM_MANAGER}:
            logger.info("User role not eligible for mentor password recovery")
            return GENERIC_REQUEST_RESPONSE, dummy_id
    else:
        return GENERIC_REQUEST_RESPONSE, dummy_id

    # Account state verification
    if not user.is_active or user.account_status == AccountStatus.SUSPENDED:
        logger.info("User account inactive or suspended during recovery request")
        return GENERIC_REQUEST_RESPONSE, dummy_id

    if user.password_hash is None:
        # User has not yet activated/created initial password
        logger.info("User has no password set during recovery request")
        return GENERIC_REQUEST_RESPONSE, dummy_id

    now = datetime.now(timezone.utc)

    # Hourly rate limit: max 5 requests per hour
    one_hour_ago = now - timedelta(hours=1)
    recent_count = count_recent_requests_for_user(db, user.id, one_hour_ago)
    if recent_count >= 5:
        logger.warning("Recovery request hourly limit exceeded for user (email omitted)")
        return GENERIC_REQUEST_RESPONSE, dummy_id

    # 60-second cooldown check
    latest = get_latest_active_request_for_user(db, user.id)
    if latest and (now - latest.last_sent_at).total_seconds() < 60:
        logger.info("Recovery request cooldown active for user (email omitted)")
        return GENERIC_REQUEST_RESPONSE, latest.id

    # Invalidate previous unconsumed requests
    invalidate_active_requests_for_user(db, user.id)

    # Generate 8-digit numeric code and HMAC hash
    code = generate_numeric_recovery_code()
    code_hash = hash_recovery_code(code)

    recovery_request = PasswordRecoveryRequest(
        user_id=user.id,
        portal=portal_normalized,
        code_hash=code_hash,
        expires_at=now + timedelta(minutes=10),
        attempt_count=0,
        last_sent_at=now,
    )

    create_recovery_request(db, recovery_request)
    db.commit()

    # Send email
    try:
        send_password_recovery_email(
            to_address=user.email,
            first_name=user.first_name,
            code=code,
            portal=portal_normalized,
        )
    except Exception:
        logger.exception("Failed to deliver password recovery email")
        # Still return generic response to prevent information leakage

    return GENERIC_REQUEST_RESPONSE, recovery_request.id


def resend_password_recovery_code(
    db: Session,
    recovery_request_id: UUID,
) -> str:
    """
    Resend an 8-digit verification code for an existing pending recovery request.
    Enforces a 60-second cooldown and generates a new code that invalidates the previous one.
    """
    request = get_recovery_request_by_id(db, recovery_request_id)
    if request is None or request.consumed_at is not None or request.verified_at is not None:
        # Dummy or consumed request: return generic acknowledgment
        return "If the recovery request is valid and eligible, a new code will be sent."

    now = datetime.now(timezone.utc)
    if (now - request.last_sent_at).total_seconds() < 60:
        raise RateLimitError("Please wait at least 60 seconds before requesting another code.")

    # Generate new code
    code = generate_numeric_recovery_code()
    request.code_hash = hash_recovery_code(code)
    request.attempt_count = 0
    request.expires_at = now + timedelta(minutes=10)
    request.last_sent_at = now
    db.commit()

    try:
        user = request.user
        send_password_recovery_email(
            to_address=user.email,
            first_name=user.first_name,
            code=code,
            portal=request.portal,
        )
    except Exception:
        logger.exception("Failed to deliver resent recovery email")

    return "A new recovery code has been sent."


def verify_password_recovery_code(
    db: Session,
    recovery_request_id: UUID,
    code: str,
) -> str:
    """
    Verify the 8-digit numeric recovery code.

    - Increments attempt count.
    - Limits to max 5 failed attempts per request.
    - On success: marks request as verified and issues a single-purpose,
      short-lived reset authorization token.
    """
    request = get_recovery_request_by_id(db, recovery_request_id)
    if request is None:
        raise InvalidRecoveryCodeError("Invalid or expired recovery code.")

    if request.consumed_at is not None:
        raise InvalidRecoveryCodeError("This recovery code has already been used.")

    if request.verified_at is not None:
        # Already verified: cannot be reverified
        raise InvalidRecoveryCodeError("This recovery request has already been verified.")

    now = datetime.now(timezone.utc)
    if now > request.expires_at:
        raise InvalidRecoveryCodeError("Recovery code has expired. Please request a new one.")

    if request.attempt_count >= 5:
        raise RecoveryAttemptLimitError(
            "Maximum verification attempts exceeded. Please request a new recovery code."
        )

    # Increment attempt count
    request.attempt_count += 1

    if not verify_recovery_code(code, request.code_hash):
        db.commit()
        remaining = max(0, 5 - request.attempt_count)
        if remaining == 0:
            raise RecoveryAttemptLimitError(
                "Maximum verification attempts exceeded. Please request a new recovery code."
            )
        raise InvalidRecoveryCodeError(
            f"Invalid recovery code. {remaining} attempt(s) remaining."
        )

    # Code is valid! Issue single-purpose reset authorization token
    raw_token = generate_reset_authorization_token()
    token_hash = hash_reset_token(raw_token)

    request.verified_at = now
    request.reset_token_hash = token_hash
    request.reset_token_expires_at = now + timedelta(minutes=15)
    db.commit()

    return raw_token


def confirm_password_reset(
    db: Session,
    reset_authorization: str,
    new_password: str,
    confirm_password: str,
) -> str:
    """
    Reset user password using single-use authorization token.

    - Validates password policy (matching, min 8 chars).
    - Updates user password hash and password_set_at (which invalidates past JWT sessions).
    - Atomically consumes reset authorization.
    - Invalidate all pending recovery requests for user.
    - Sends confirmation email notice.
    """
    if new_password != confirm_password:
        raise WeakPasswordError("Passwords do not match.")

    if len(new_password) < 8:
        raise WeakPasswordError("Password must be at least 8 characters.")

    token_hash = hash_reset_token(reset_authorization)
    request = get_recovery_request_by_reset_token_hash(db, token_hash)
    if request is None:
        raise InvalidResetAuthorizationError("Invalid or expired password reset authorization.")

    now = datetime.now(timezone.utc)
    if request.consumed_at is not None:
        raise InvalidResetAuthorizationError(
            "This password reset authorization has already been used."
        )

    if request.reset_token_expires_at is None or now > request.reset_token_expires_at:
        raise InvalidResetAuthorizationError(
            "Password reset authorization has expired. Please request a new code."
        )

    user = request.user
    user.password_hash = hash_password(new_password)
    user.password_set_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now

    request.consumed_at = now

    # Invalidate all remaining recovery requests for this user
    invalidate_active_requests_for_user(db, user.id)

    db.commit()

    # Send confirmation email
    try:
        send_password_changed_email(
            to_address=user.email,
            first_name=user.first_name,
        )
    except Exception:
        logger.exception("Failed to deliver password change confirmation email")

    return "Password has been updated successfully. You can now log in using your new password."
