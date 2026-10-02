"""
Authentication service.

Covers:
- Admin creating a student (invitation-based onboarding)
- Account activation (token → password)
- 2FA setup and confirmation
- Two-stage login (password → challenge token → TOTP → session)
- Current user retrieval
"""

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import (
    create_2fa_challenge_token,
    create_access_token,
    decode_2fa_challenge_token,
    generate_totp_secret,
    get_totp_uri,
    hash_invitation_token,
    hash_password,
    verify_password,
    verify_totp,
)
from app.models.user import AccountStatus, User, UserRole
from app.models.user_recovery_code import UserRecoveryCode
from app.repositories.invitation import (
    get_invitation_by_token_hash,
    mark_invitation_used,
)
from app.repositories.recovery_code import (
    create_recovery_codes,
    delete_all_recovery_codes_for_user,
    get_unused_recovery_code_by_hash,
)
from app.repositories.user import (
    create_user,
    get_user_by_email,
    get_user_by_id,
)
from app.schemas.auth import (
    ActivateAccountRequest,
    CreateFellowRequest,
    CreateStudentRequest,
    TwoFASetupResponse,
    VerifyTOTPRequest,
)
from app.services.invitation import create_invitation_for_user

_RECOVERY_CODE_COUNT = 8
_RECOVERY_CODE_LENGTH = 10  # characters per code


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for authentication-related errors."""


class DuplicateEmailError(AuthError):
    pass


class InvalidInvitationError(AuthError):
    pass


class WeakPasswordError(AuthError):
    pass


class AccountNotInvitedError(AuthError):
    pass


class AccountSuspendedError(AuthError):
    pass


class InvalidCredentialsError(AuthError):
    pass


class TwoFANotConfiguredError(AuthError):
    pass


class InvalidTwoFACodeError(AuthError):
    pass


# ---------------------------------------------------------------------------
# Admin: create Fellow
# ---------------------------------------------------------------------------


def admin_create_fellow(
    db: Session,
    data: "CreateFellowRequest | CreateStudentRequest",
) -> tuple[User, str]:
    """
    Create a new Fellow user in the ``invited`` state.

    Returns ``(user, raw_token)`` so the caller can pass the raw token
    to the invitation email service.

    The raw token is never stored — only its SHA-256 hash is persisted.
    The transaction is committed inside this function after all writes
    succeed.
    """
    normalized_email = data.email.strip().lower()

    existing = get_user_by_email(db, normalized_email)
    if existing:
        raise DuplicateEmailError(
            f"A user with email {normalized_email!r} already exists."
        )

    user = User(
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        email=normalized_email,
        password_hash=None,
        role=UserRole.FELLOW,
        account_status=AccountStatus.INVITED,
        two_factor_enabled=False,
    )

    create_user(db, user)

    # create_user commits, so we need to work within a follow-up flush.
    # Generate invitation token (flushed, not committed yet).
    raw_token = create_invitation_for_user(db, user)

    db.commit()
    db.refresh(user)

    return user, raw_token


# Backwards-compatible alias
admin_create_student = admin_create_fellow


# ---------------------------------------------------------------------------
# Account activation
# ---------------------------------------------------------------------------

_MIN_PASSWORD_LENGTH = 8


def activate_account(
    db: Session,
    data: "ActivateAccountRequest",
) -> User:
    """
    Activate a student account using the invitation token.

    Steps:
    1. Hash the received token and look it up.
    2. Validate expiry and single-use constraint.
    3. Validate the chosen password.
    4. Hash and store the password.
    5. Set email_verified_at, password_set_at.
    6. Mark invitation token as used.
    7. account_status remains ``invited`` until 2FA is confirmed.
    8. Commit the transaction.
    """
    if data.password != data.confirm_password:
        raise WeakPasswordError("Passwords do not match.")

    if len(data.password) < _MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters."
        )

    token_hash = hash_invitation_token(data.token)
    invitation = get_invitation_by_token_hash(db, token_hash)

    if invitation is None:
        raise InvalidInvitationError("Invalid or expired activation link.")

    if invitation.used_at is not None:
        raise InvalidInvitationError("This activation link has already been used.")

    now = datetime.now(timezone.utc)
    if invitation.expires_at.replace(tzinfo=timezone.utc) < now:
        raise InvalidInvitationError("This activation link has expired.")

    user = get_user_by_id(
        db,
        invitation.user_id,
    )

    if user is None:
        raise InvalidInvitationError(
            "User not found."
        )

    if user.account_status != AccountStatus.INVITED:
        raise AccountNotInvitedError(
            "This account has already been activated or is suspended."
        )
    user.password_hash = hash_password(data.password)
    user.password_set_at = now
    user.email_verified_at = now

    # Fellow remains INVITED until mandatory 2FA setup is confirmed.
    user.account_status = AccountStatus.INVITED
    user.is_active = True
    user.two_factor_enabled = False
    user.totp_secret = None

    mark_invitation_used(db, invitation)
    

    db.commit()
    db.refresh(user)

    return user


def resume_onboarding(
    db: Session,
    email: str,
    password: str,
) -> User:
    """Authenticate an incomplete invite and allow only its 2FA setup to resume."""
    user = get_user_by_email(db, email.strip().lower())

    if (
        user is None
        or user.password_hash is None
        or not verify_password(password, user.password_hash)
        or not user.is_active
        or user.role not in {UserRole.FELLOW, UserRole.STUDENT}
        or user.account_status != AccountStatus.INVITED
        or user.password_set_at is None
        or user.email_verified_at is None
        or user.two_factor_enabled
    ):
        # One response covers unknown email, wrong password, completed onboarding,
        # suspended accounts, and invalid state so this endpoint is not an
        # account-enumeration oracle.
        raise InvalidCredentialsError(
            "Unable to resume onboarding with the supplied credentials."
        )

    return user


# ---------------------------------------------------------------------------
# 2FA setup
# ---------------------------------------------------------------------------


def setup_2fa(
    db: Session,
    user: User,
) -> "TwoFASetupResponse":
    """
    Generate a new TOTP secret for the user and return the QR URI.

    The secret is stored immediately on the user record so it can be
    confirmed in the next step.  ``two_factor_enabled`` remains ``False``
    until the first code is successfully verified.
    """
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, user.email)

    user.totp_secret = secret
    db.commit()

    return TwoFASetupResponse(
        totp_uri=uri,
        # Return the secret as a plain string for manual entry in apps
        # that don't support QR scanning.  This is the only point it is
        # ever exposed outside the database.
        secret=secret,
    )


def confirm_2fa(
    db: Session,
    user: User,
    code: str,
) -> list[str]:
    """
    Verify the first TOTP code after setup and activate the account.

    Returns a list of plain-text recovery codes (shown once only).

    Raises ``InvalidTwoFACodeError`` if the code is wrong.
    """
    if not user.totp_secret:
        raise TwoFANotConfiguredError("2FA has not been initialised for this account.")

    if not verify_totp(user.totp_secret, code):
        raise InvalidTwoFACodeError("Invalid verification code.")

    plain_codes = _generate_recovery_codes()

    # Remove any existing codes (e.g. from a previous incomplete setup)
    delete_all_recovery_codes_for_user(db, user.id)

    hashed_codes = [
        UserRecoveryCode(
            user_id=user.id,
            code_hash=_hash_recovery_code(c),
        )
        for c in plain_codes
    ]
    create_recovery_codes(db, hashed_codes)

    user.two_factor_enabled = True
    user.account_status = AccountStatus.ACTIVE

    db.commit()
    db.refresh(user)

    return plain_codes


# ---------------------------------------------------------------------------
# Login — stage 1: password verification
# ---------------------------------------------------------------------------


def verify_login_credentials(
    db: Session,
    email: str,
    password: str,
) -> tuple[User, str | None, str | None]:
    """
    Validate email + password.

    Returns:
        (user, challenge_token, access_token)

    Fellows must always complete 2FA before receiving
    a normal access token.

    Non-Fellow roles may continue without 2FA unless
    2FA has explicitly been enabled for their account.
    """

    normalized_email = email.strip().lower()

    user = get_user_by_email(
        db,
        normalized_email,
    )

    if user is None or user.password_hash is None:
        raise InvalidCredentialsError(
            "Invalid email or password."
        )

    if not verify_password(
        password,
        user.password_hash,
    ):
        raise InvalidCredentialsError(
            "Invalid email or password."
        )

    if not user.is_active:
        raise InvalidCredentialsError(
            "Account is inactive."
        )

    if user.account_status == AccountStatus.SUSPENDED:
        raise AccountSuspendedError(
            "This account has been suspended."
        )

    if user.account_status == AccountStatus.INVITED:
        raise AccountNotInvitedError(
            "Account setup is incomplete. "
            "Please check your invitation email."
        )

    # ---------------------------------------------------------
    # Fellows MUST use 2FA
    # ---------------------------------------------------------
    if user.role in {
        UserRole.FELLOW,
        UserRole.STUDENT,
    }:
        if (
            not user.two_factor_enabled
            or not user.totp_secret
        ):
            raise TwoFANotConfiguredError(
                "Two-factor authentication setup is incomplete."
            )

        challenge_token = create_2fa_challenge_token(
            str(user.id)
        )

        return user, challenge_token, None

    # ---------------------------------------------------------
    # Other roles
    # If they have 2FA enabled, require it.
    # ---------------------------------------------------------
    if user.two_factor_enabled:
        if not user.totp_secret:
            raise TwoFANotConfiguredError(
                "Two-factor authentication configuration is incomplete."
            )

        challenge_token = create_2fa_challenge_token(
            str(user.id)
        )

        return user, challenge_token, None

    # ---------------------------------------------------------
    # Non-Fellow account without mandatory 2FA
    # ---------------------------------------------------------
    user.last_login_at = datetime.now(timezone.utc)

    db.commit()

    access_token = create_access_token(
        str(user.id),
        role=user.role,
    )

    return user, None, access_token

# ---------------------------------------------------------------------------
# Login — stage 2: TOTP verification
# ---------------------------------------------------------------------------


def complete_2fa_login(
    db: Session,
    challenge_token: str,
    code: str,
) -> tuple[User, str]:
    """
    Verify the TOTP code using a challenge token and issue a full access token.

    Returns ``(user, access_token)``.

    Raises ``InvalidTwoFACodeError`` for bad codes.
    """
    import jwt  # local import to avoid circular deps at module load

    try:
        payload = decode_2fa_challenge_token(challenge_token)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise InvalidCredentialsError("Invalid or expired challenge token.") from exc
    user = get_user_by_id(db, user_id)

    

    if user is None:
        raise InvalidCredentialsError("User not found.")

    if not user.is_active:
        raise InvalidCredentialsError(
            "Account is inactive."
        )

    if user.account_status == AccountStatus.SUSPENDED:
        raise AccountSuspendedError(
            "This account has been suspended."
        )

    if user.account_status != AccountStatus.ACTIVE:
        raise InvalidCredentialsError(
            "Account setup is incomplete."
        )

    if not user.two_factor_enabled:
        raise TwoFANotConfiguredError(
            "Two-factor authentication is not enabled."
        )

    # Allow recovery code or dev verification code as alternative to TOTP
    if not (
        user.totp_secret
        and verify_totp(
            user.totp_secret,
            code,
        )
    ):
        code_hash = _hash_recovery_code(code)

        recovery = get_unused_recovery_code_by_hash(
            db,
            user.id,
            code_hash,
        )

        if recovery is None:
            raise InvalidTwoFACodeError(
                "Invalid authentication code."
            )

        # Recovery codes are strictly one-time use.
        recovery.used = True
        recovery.used_at = datetime.now(timezone.utc)
        db.flush()

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    access_token = create_access_token(str(user.id), role=user.role)
    return user, access_token


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


def get_current_user_by_id(
    db: Session,
    user_id: UUID,
) -> User | None:
    """Load and return a user by their UUID."""
    return get_user_by_id(db, user_id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _generate_recovery_codes() -> list[str]:
    """Generate a list of random alphanumeric recovery codes."""
    return [
        secrets.token_urlsafe(_RECOVERY_CODE_LENGTH)[:_RECOVERY_CODE_LENGTH]
        for _ in range(_RECOVERY_CODE_COUNT)
    ]


def _hash_recovery_code(code: str) -> str:
    """SHA-256 hash of a recovery code."""
    return hashlib.sha256(code.encode()).hexdigest()
