"""
Authentication routes.

POST /api/v1/auth/activate       — activate account with invitation token
POST /api/v1/auth/2fa/setup      — generate TOTP secret after activation
POST /api/v1/auth/2fa/confirm    — verify first TOTP code and activate account
POST /api/v1/auth/login          — stage 1: validate password, return challenge token
POST /api/v1/auth/2fa/verify     — stage 2: verify TOTP, issue access token
GET  /api/v1/auth/me             — return current user profile
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import (
    require_authenticated_user,
    require_onboarding_user,
)
from app.core.security import (
    create_access_token,
    create_onboarding_token,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    ActivateAccountRequest,
    ActivateAccountResponse,
    ConfirmTwoFARequest,
    ConfirmTwoFAResponse,
    LoginRequest,
    LoginResponse,
    OnboardingResumeRequest,
    OnboardingResumeResponse,
    TokenResponse,
    TwoFASetupResponse,
    UserResponse,
    VerifyTOTPRequest,
)
from app.services.auth import (
    AccountNotInvitedError,
    AccountSuspendedError,
    InvalidCredentialsError,
    InvalidInvitationError,
    InvalidTwoFACodeError,
    TwoFANotConfiguredError,
    WeakPasswordError,
    activate_account,
    complete_2fa_login,
    confirm_2fa,
    setup_2fa,
    resume_onboarding,
    verify_login_credentials,
)


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


# ---------------------------------------------------------------------------
# Account activation
# ---------------------------------------------------------------------------


@router.post(
    "/activate",
    response_model=ActivateAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate account with invitation token",
)
def activate(
    data: ActivateAccountRequest,
    db: Session = Depends(get_db),
) -> ActivateAccountResponse:
    """
    Step 1 of student onboarding.

    Validates the invitation token, sets the password, and marks the
    email as verified.  The account is **not yet active** — the student
    must complete 2FA setup next.
    """
    try:
        user = activate_account(db, data)
    except (InvalidInvitationError, AccountNotInvitedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    onboarding_token = create_onboarding_token(
        str(user.id)
    )

    return ActivateAccountResponse(
        message=(
            "Password created successfully. "
            "Complete two-factor authentication setup."
        ),
        user_id=user.id,
        email=user.email,
        onboarding_token=onboarding_token,
    )


@router.post(
    "/onboarding/resume",
    response_model=OnboardingResumeResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume incomplete 2FA onboarding",
)
def resume_incomplete_onboarding(
    data: OnboardingResumeRequest,
    db: Session = Depends(get_db),
) -> OnboardingResumeResponse:
    try:
        user = resume_onboarding(db, str(data.email), data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resume onboarding with the supplied credentials.",
        ) from exc

    return OnboardingResumeResponse(
        message="Continue two-factor authentication setup.",
        onboarding_token=create_onboarding_token(str(user.id)),
    )


# ---------------------------------------------------------------------------
# 2FA setup
# ---------------------------------------------------------------------------


@router.post(
    "/2fa/setup",
    response_model=TwoFASetupResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate TOTP secret for 2FA setup",
)
def two_fa_setup(
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_onboarding_user
    )
) -> TwoFASetupResponse:
    """
    Step 2 of student onboarding (also usable for 2FA reset).

    Generates a new TOTP secret, stores it on the user record, and
    returns the QR URI and secret for manual entry.

    The student must scan the QR code and confirm the first code via
    POST /auth/2fa/confirm before the account becomes active.
    """
    return setup_2fa(db, current_user)


@router.post(
    "/2fa/confirm",
    response_model=ConfirmTwoFAResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm TOTP setup and activate account",
)

def two_fa_confirm(
    data: ConfirmTwoFARequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_onboarding_user),
) -> ConfirmTwoFAResponse:
    try:
        plain_codes = confirm_2fa(
            db,
            current_user,
            data.code,
        )

    except TwoFANotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except InvalidTwoFACodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    access_token = create_access_token(
        str(current_user.id),
        role=current_user.role,
    )

    return ConfirmTwoFAResponse(
        message=(
            "Two-factor authentication enabled. "
            "Store your recovery codes safely."
        ),
        recovery_codes=plain_codes,
        access_token=access_token,
    )

# ---------------------------------------------------------------------------
# Login — stage 1
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Stage 1 login: verify password and return 2FA challenge token",
)
def login(
    data: LoginRequest,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """
    Stage 1 of the two-stage login flow.

    On success, returns a short-lived challenge token.  The client must
    exchange this for a full access token by completing TOTP verification
    via POST /auth/2fa/verify.
    """
    try:
        _user, challenge_token, access_token = verify_login_credentials(
            db,
            str(data.email),
            data.password,
        )
    except (InvalidCredentialsError,) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        ) from exc
    except AccountSuspendedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountNotInvitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except TwoFANotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if access_token:
        return LoginResponse(
            requires_2fa=False,
            access_token=access_token,
        )

    return LoginResponse(
        requires_2fa=True,
        challenge_token=challenge_token,
    )


# ---------------------------------------------------------------------------
# Login — stage 2 (TOTP / recovery code)
# ---------------------------------------------------------------------------


@router.post(
    "/2fa/verify",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Stage 2 login: verify TOTP code and issue access token",
)

def two_fa_verify(
    data: VerifyTOTPRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    try:
        _user, access_token = complete_2fa_login(
            db,
            data.challenge_token,
            data.code,
        )

    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    except InvalidTwoFACodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication code.",
        ) from exc

    except AccountSuspendedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    except TwoFANotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    return TokenResponse(
        access_token=access_token,
    )


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Return current authenticated user profile",
)
def me(
    current_user: User = Depends(require_authenticated_user),
) -> UserResponse:
    """
    Return the profile of the currently authenticated user.

    Never exposes password_hash, totp_secret, or raw tokens.
    """
    return UserResponse.model_validate(current_user)
