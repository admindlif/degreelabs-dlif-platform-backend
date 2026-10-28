"""
FastAPI authorization dependencies.

Usage in route functions:

    from app.core.permissions import require_authenticated_user, require_roles

    @router.get("/me")
    def me(user: User = Depends(require_authenticated_user)):
        ...

    @router.post("/admin/students")
    def create_student(
        user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    ):
        ...

Authorization is enforced server-side for every protected endpoint.
Clients authenticate with the HttpOnly access cookie.  Bearer tokens remain
supported as a temporary compatibility fallback for existing API consumers.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_cookies import (
    ACCESS_COOKIE_NAME,
    MFA_ENROLLMENT_COOKIE_NAME,
    ONBOARDING_COOKIE_NAME,
)
from app.core.config import settings
from app.core.security import (
    decode_access_token,
    decode_mfa_enrollment_token,
    decode_onboarding_token,
)
from app.db.session import get_db
from app.models.user import AccountStatus, User, UserRole
from app.repositories.user import get_user_by_id
from app.models.auth_session import AuthSession
from app.services.auth_session import (
    AuthSessionError,
    RequestMetadata,
    get_active_session_for_access,
    hash_client_identifier,
    sanitize_user_agent,
    revoke_all_user_sessions,
)

from uuid import UUID
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_user_id_from_token(
    token: str | None,
) -> str:
    """
    Validate an access token and return the user ID (``sub`` claim).

    Raises HTTP 401 for missing or invalid tokens.
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload["sub"]


def _decode_access_payload(token: str | None) -> dict:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _request_metadata(request: Request) -> RequestMetadata:
    client_host = request.client.host if request.client else None
    return RequestMetadata(
        ip_address_hash=hash_client_identifier(client_host),
        user_agent=sanitize_user_agent(request.headers.get("user-agent")),
    )


def _extract_onboarding_user_id_from_token(
    token: str | None,
) -> str:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Onboarding authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_onboarding_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Onboarding session has expired.",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid onboarding token.",
        )

    return payload["sub"]


def require_onboarding_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Allow only Fellows, legacy Students, and Mentors who have set their
    password but have not yet completed 2FA onboarding.
    """

    user_id_str = _extract_onboarding_user_id_from_token(
        request.cookies.get(ONBOARDING_COOKIE_NAME)
    )

    try:
        user_id = UUID(user_id_str)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid onboarding token subject.",
        )

    user = get_user_by_id(
        db,
        user_id,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if user.role not in {
        UserRole.FELLOW,
        UserRole.STUDENT,
        UserRole.MENTOR,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account role is not eligible for onboarding.",
        )

    if user.account_status != AccountStatus.INVITED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account onboarding has already been completed.",
        )

    if not user.password_set_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password setup is incomplete.",
        )

    return user


def require_2fa_setup_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Allow invited onboarding or controlled privileged MFA enrollment."""
    if request.cookies.get(ONBOARDING_COOKIE_NAME):
        return require_onboarding_user(request, db)

    enrollment_token = request.cookies.get(MFA_ENROLLMENT_COOKIE_NAME)
    if enrollment_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor authentication enrollment is required.",
        )
    try:
        payload = decode_mfa_enrollment_token(enrollment_token)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA enrollment session.",
        ) from exc

    user = get_user_by_id(db, user_id)
    if (
        user is None
        or user.role
        not in {
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
            UserRole.PROGRAM_MANAGER,
        }
        or not user.is_active
        or user.account_status != AccountStatus.ACTIVE
        or user.password_set_at is None
        or user.two_factor_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not eligible for MFA enrollment.",
        )
    return user

# ---------------------------------------------------------------------------
# Base dependency: authenticated user
# ---------------------------------------------------------------------------


def require_authenticated_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency that returns the current authenticated user.

    - Prefers the HttpOnly access cookie.
    - Accepts a Bearer token as a compatibility fallback.
    - Loads the user from the database.
    - Rejects suspended or inactive accounts.
    """
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    if token is None and credentials is not None:
        token = credentials.credentials

    payload = _decode_access_payload(token)
    user_id_str = payload.get("sub")

    try:
        user_id = UUID(user_id_str)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
        )

    user = get_user_by_id(db, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if not user.is_active:
        revoke_all_user_sessions(db, user.id, reason="account_inactive")
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive.",
        )

    if user.account_status == AccountStatus.SUSPENDED:
        revoke_all_user_sessions(db, user.id, reason="account_suspended")
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been suspended.",
        )

    if user.account_status != AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account setup is incomplete.",
        )

    if user.password_set_at is not None and token is not None:
        try:
            if "iat" in payload:
                token_iat_ts = float(payload["iat"])
                if token_iat_ts < user.password_set_at.timestamp():
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session has been revoked due to a password change. Please sign in again.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
        except HTTPException:
            raise
        except Exception:
            pass

    session_id_claim = payload.get("sid")
    if session_id_claim is None:
        if not settings.allow_legacy_sessionless_access_tokens:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A server-managed authentication session is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.auth_session = None
        return user

    try:
        session_id = UUID(str(session_id_claim))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication session identifier.",
        ) from exc

    try:
        auth_session = get_active_session_for_access(
            db,
            session_id,
            user,
            metadata=_request_metadata(request),
        )
    except AuthSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    request.state.auth_session = auth_session

    return user


def require_authenticated_session(
    request: Request,
    _current_user: User = Depends(require_authenticated_user),
) -> AuthSession:
    session = getattr(request.state, "auth_session", None)
    if not isinstance(session, AuthSession):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A server-managed authentication session is required.",
        )
    return session


# ---------------------------------------------------------------------------
# Role-based dependency factory
# ---------------------------------------------------------------------------


def require_roles(*roles: UserRole):
    """
    Return a FastAPI dependency that allows only the specified roles.

    Example::

        Depends(require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN))
    """

    def _check_roles(
        current_user: User = Depends(require_authenticated_user),
    ) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return _check_roles


# ---------------------------------------------------------------------------
# Portal-specific dependencies
#
# Each API runtime imports the relevant guard to enforce that only users
# with the correct role can access that portal's protected endpoints.
# The role is read from the JWT claim — no extra DB query needed.
# ---------------------------------------------------------------------------


def require_fellow_portal(
    current_user: User = Depends(require_authenticated_user),
) -> User:
    """
    Allow FELLOW role (and legacy STUDENT role).
    Import in apps/student_api routes.
    """
    if current_user.role not in {UserRole.FELLOW, UserRole.STUDENT}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to the Fellow Portal.",
        )
    return current_user


# Backwards-compatible alias
require_student_portal = require_fellow_portal


def require_mentor_portal(
    current_user: User = Depends(require_authenticated_user),
) -> User:
    """
    Allow MENTOR and PROGRAM_MANAGER roles.
    Import in apps/mentor_api routes.
    """
    _mentor_roles = {UserRole.MENTOR, UserRole.PROGRAM_MANAGER}
    if current_user.role not in _mentor_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to the Mentor Portal.",
        )
    return current_user


def require_admin_portal(
    current_user: User = Depends(require_authenticated_user),
) -> User:
    """
    Allow ADMIN and SUPER_ADMIN roles.
    Import in apps/admin_api routes.
    """
    _admin_roles = {UserRole.ADMIN, UserRole.SUPER_ADMIN}
    if current_user.role not in _admin_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to the Admin Portal.",
        )
    return current_user
