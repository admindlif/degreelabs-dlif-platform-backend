"""Origin validation for state-changing cookie-authenticated requests."""

from fastapi import Request

from app.core.auth_cookies import (
    ACCESS_COOKIE_NAME,
    MFA_ENROLLMENT_COOKIE_NAME,
    ONBOARDING_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    TWO_FA_CHALLENGE_COOKIE_NAME,
)
from app.core.config import settings


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_AUTH_COOKIE_NAMES = {
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    ONBOARDING_COOKIE_NAME,
    TWO_FA_CHALLENGE_COOKIE_NAME,
    MFA_ENROLLMENT_COOKIE_NAME,
}


def request_origin_error(request: Request) -> str | None:
    """Return a safe error message when an unsafe browser origin is invalid."""
    if request.method.upper() in _SAFE_METHODS:
        return None

    origin = request.headers.get("origin")
    if origin:
        normalized = origin.strip().rstrip("/")
        if normalized not in settings.allowed_origins:
            return "Request origin is not allowed."
        return None

    # Non-browser Bearer/CLI requests may legitimately omit Origin. Browser
    # requests carrying any authentication cookie must provide it, preventing
    # state-changing cookie requests from untrusted sites.
    if any(name in request.cookies for name in _AUTH_COOKIE_NAMES):
        return "Origin header is required for cookie-authenticated requests."

    return None
