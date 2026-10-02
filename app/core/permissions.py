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
Clients must pass a valid Bearer token in the Authorization header.
"""

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import (
    decode_access_token,
    decode_onboarding_token,
)
from app.db.session import get_db
from app.models.user import AccountStatus, User, UserRole
from app.repositories.user import get_user_by_id

from uuid import UUID
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_user_id_from_token(
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    """
    Validate the Bearer token and return the user ID (``sub`` claim).

    Raises HTTP 401 for missing or invalid tokens.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
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


def _extract_onboarding_user_id_from_token(
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Onboarding authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_onboarding_token(
            credentials.credentials
        )
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
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> User:
    """
    Allow only Fellows who have set their password but
    have not yet completed 2FA onboarding.
    """

    user_id_str = _extract_onboarding_user_id_from_token(
        credentials
    )

    try:
        user_id = UUID(user_id_str)
    except ValueError:
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
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Onboarding is restricted to Fellows.",
        )

    if user.account_status != AccountStatus.INVITED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fellow onboarding has already been completed.",
        )

    if not user.password_set_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password setup is incomplete.",
        )

    return user

# ---------------------------------------------------------------------------
# Base dependency: authenticated user
# ---------------------------------------------------------------------------


def require_authenticated_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency that returns the current authenticated user.

    - Validates the Bearer token.
    - Loads the user from the database.
    - Rejects suspended or inactive accounts.
    """
    user_id_str = _extract_user_id_from_token(credentials)

    try:
        user_id = UUID(user_id_str)
    except ValueError:
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive.",
        )

    if user.account_status == AccountStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been suspended.",
        )

    if user.account_status != AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account setup is incomplete.",
        )

    return user


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
