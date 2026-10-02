"""
Core security utilities.

Responsibilities:
- Password hashing (Argon2 via pwdlib)
- JWT encoding / decoding
- Invitation token generation and SHA-256 hashing
- TOTP secret generation and OTP verification
- 2FA challenge token generation / verification

Never log passwords, raw tokens, or TOTP secrets.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
import pyotp
from pwdlib import PasswordHash

from app.core.config import settings
from app.models.user import UserRole


_password_hash = PasswordHash.recommended()

ALGORITHM = "HS256"

# Token type claim used to distinguish JWT purposes.
_TOKEN_TYPE_ACCESS = "access"
_TOKEN_TYPE_ONBOARDING = "onboarding"
_TOKEN_TYPE_2FA_CHALLENGE = "2fa_challenge"


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2."""
    return _password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    """Verify a plaintext password against an Argon2 hash."""
    return _password_hash.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT — access tokens
# ---------------------------------------------------------------------------


def create_access_token(
    subject: str,
    role: UserRole | str | None = None,
) -> str:
    """
    Create a signed JWT access token for the given user ID.

    ``subject`` must be the user UUID as a string.
    ``role`` is optionally embedded as a claim so portal APIs can gate access
    without a database lookup on every request.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )

    payload: dict = {
        "sub": subject,
        "exp": expires_at,
        "type": _TOKEN_TYPE_ACCESS,
    }
    if role is not None:
        payload["role"] = role.value if isinstance(role, UserRole) else str(role)

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.

    Raises ``jwt.PyJWTError`` on invalid or expired tokens.
    """
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[ALGORITHM],
    )

    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        raise jwt.InvalidTokenError("Token type mismatch")

    return payload


# ---------------------------------------------------------------------------
# JWT — onboarding tokens
# ---------------------------------------------------------------------------


def create_onboarding_token(user_id: str) -> str:
    """
    Create a short-lived token that is valid only for completing
    Fellow onboarding / 2FA setup.

    It must never be accepted by normal portal endpoints.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.onboarding_token_expire_minutes
    )

    payload = {
        "sub": user_id,
        "exp": expires_at,
        "type": _TOKEN_TYPE_ONBOARDING,
    }

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_onboarding_token(token: str) -> dict:
    """
    Decode a Fellow onboarding token.
    """
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[ALGORITHM],
    )

    if payload.get("type") != _TOKEN_TYPE_ONBOARDING:
        raise jwt.InvalidTokenError("Token type mismatch")

    return payload


# ---------------------------------------------------------------------------
# JWT — 2FA challenge tokens (short-lived, single-purpose)
# ---------------------------------------------------------------------------


def create_2fa_challenge_token(user_id: str) -> str:
    """
    Create a short-lived JWT challenge token issued after successful
    password verification but before 2FA completion.

    Must not be accepted as a full authentication token.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.two_fa_challenge_expire_minutes
    )

    payload = {
        "sub": user_id,
        "exp": expires_at,
        "type": _TOKEN_TYPE_2FA_CHALLENGE,
    }

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_2fa_challenge_token(token: str) -> dict:
    """
    Decode and validate a 2FA challenge token.

    Raises ``jwt.PyJWTError`` on invalid or expired tokens.
    """
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[ALGORITHM],
    )

    if payload.get("type") != _TOKEN_TYPE_2FA_CHALLENGE:
        raise jwt.InvalidTokenError("Token type mismatch")

    return payload


# ---------------------------------------------------------------------------
# Invitation tokens
# ---------------------------------------------------------------------------


def generate_invitation_token() -> str:
    """
    Generate a cryptographically secure random URL-safe token.

    Returns the **raw** token.  Only the SHA-256 hash of this value
    should be persisted in the database.
    """
    return secrets.token_urlsafe(32)


def hash_invitation_token(raw_token: str) -> str:
    """
    Return the SHA-256 hex-digest of a raw invitation token.

    This is what gets stored in ``user_invitation_tokens.token_hash``.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Generate a new base32-encoded TOTP secret."""
    return pyotp.random_base32()


def get_totp_uri(
    secret: str,
    email: str,
) -> str:
    """
    Build the otpauth:// URI for QR-code generation.

    ``email`` is used as the account label.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(
        name=email,
        issuer_name=settings.totp_issuer,
    )


def verify_totp(secret: str, code: str) -> bool:
    """
    Verify a 6-digit TOTP code against the stored secret.

    Allows a one-period drift (valid_window=1) to accommodate clock skew.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)