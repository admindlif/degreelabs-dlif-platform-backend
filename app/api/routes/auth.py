"""Authentication and Fellow onboarding routes."""

from io import BytesIO

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.auth_cookies import (
    TWO_FA_CHALLENGE_COOKIE_NAME,
    clear_access_cookie,
    clear_all_auth_cookies,
    clear_onboarding_cookie,
    clear_two_fa_challenge_cookie,
    set_access_cookie,
    set_onboarding_cookie,
    set_two_fa_challenge_cookie,
)
from app.core.permissions import (
    require_authenticated_user,
    require_onboarding_user,
)
from app.core.security import (
    create_access_token,
    create_onboarding_token,
    get_totp_uri,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    ActivateAccountRequest,
    ActivateAccountResponse,
    AuthenticationResponse,
    ConfirmTwoFARequest,
    ConfirmTwoFAResponse,
    LoginRequest,
    LoginResponse,
    OnboardingResumeRequest,
    OnboardingResumeResponse,
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
    resume_onboarding,
    setup_2fa,
    verify_login_credentials,
)


router = APIRouter(prefix="/auth", tags=["Authentication"])


def _prevent_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/activate",
    response_model=ActivateAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate account with invitation token",
)
def activate(
    data: ActivateAccountRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ActivateAccountResponse:
    """Set the invited Fellow's password and begin the 2FA setup session."""
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

    set_onboarding_cookie(response, create_onboarding_token(str(user.id)))
    clear_access_cookie(response)
    clear_two_fa_challenge_cookie(response)
    _prevent_caching(response)

    return ActivateAccountResponse(
        message=(
            "Password created successfully. "
            "Complete two-factor authentication setup."
        ),
        user_id=user.id,
        email=user.email,
    )


@router.post(
    "/onboarding/resume",
    response_model=OnboardingResumeResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume incomplete 2FA onboarding",
)
def resume_incomplete_onboarding(
    data: OnboardingResumeRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> OnboardingResumeResponse:
    try:
        user = resume_onboarding(db, str(data.email), data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resume onboarding with the supplied credentials.",
        ) from exc

    set_onboarding_cookie(response, create_onboarding_token(str(user.id)))
    clear_access_cookie(response)
    clear_two_fa_challenge_cookie(response)
    _prevent_caching(response)

    return OnboardingResumeResponse(
        message="Continue two-factor authentication setup.",
    )


@router.post(
    "/2fa/setup",
    response_model=TwoFASetupResponse,
    status_code=status.HTTP_200_OK,
    summary="Initialize TOTP setup",
)
def two_fa_setup(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_onboarding_user),
) -> TwoFASetupResponse:
    """Create a pending TOTP seed without exposing it in the API response."""
    setup_2fa(db, current_user)
    _prevent_caching(response)
    return TwoFASetupResponse(
        message="Two-factor authentication setup initialized.",
        qr_code_url="/api/v1/auth/2fa/qr",
    )


@router.get(
    "/2fa/qr",
    status_code=status.HTTP_200_OK,
    summary="Return the pending TOTP setup QR code",
    responses={200: {"content": {"image/png": {}}}},
)
def two_fa_qr(
    current_user: User = Depends(require_onboarding_user),
) -> Response:
    """Render the pending provisioning URI as a non-cacheable PNG image."""
    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication setup has not been initialized.",
        )

    provisioning_uri = get_totp_uri(
        current_user.totp_secret,
        current_user.email,
    )
    image = qrcode.make(provisioning_uri)
    output = BytesIO()
    image.save(output, format="PNG")

    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/2fa/confirm",
    response_model=ConfirmTwoFAResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm TOTP setup and activate account",
)
def two_fa_confirm(
    data: ConfirmTwoFARequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_onboarding_user),
) -> ConfirmTwoFAResponse:
    try:
        plain_codes = confirm_2fa(db, current_user, data.code)
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
    set_access_cookie(response, access_token)
    clear_onboarding_cookie(response)
    clear_two_fa_challenge_cookie(response)
    _prevent_caching(response)

    return ConfirmTwoFAResponse(
        message="Two-factor authentication enabled.",
        recovery_codes=plain_codes,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Stage 1 login: verify password",
)
def login(
    data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Validate credentials and establish the next session cookie."""
    try:
        _user, challenge_token, access_token = verify_login_credentials(
            db,
            str(data.email),
            data.password,
        )
    except InvalidCredentialsError as exc:
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

    clear_onboarding_cookie(response)
    _prevent_caching(response)

    if access_token is not None:
        set_access_cookie(response, access_token)
        clear_two_fa_challenge_cookie(response)
        return LoginResponse(requires_2fa=False)

    if challenge_token is None:
        raise RuntimeError("Authentication service did not issue a session token.")

    clear_access_cookie(response)
    set_two_fa_challenge_cookie(response, challenge_token)
    return LoginResponse(requires_2fa=True)


@router.post(
    "/2fa/verify",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_200_OK,
    summary="Stage 2 login: verify TOTP or recovery code",
)
def two_fa_verify(
    data: VerifyTOTPRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthenticationResponse:
    challenge_token = request.cookies.get(TWO_FA_CHALLENGE_COOKIE_NAME)
    if challenge_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor authentication challenge required.",
        )

    try:
        _user, access_token = complete_2fa_login(
            db,
            challenge_token,
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

    set_access_cookie(response, access_token)
    clear_two_fa_challenge_cookie(response)
    _prevent_caching(response)
    return AuthenticationResponse(message="Authentication successful.")


@router.post(
    "/logout",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_200_OK,
    summary="Clear all authentication sessions",
)
def logout(response: Response) -> AuthenticationResponse:
    clear_all_auth_cookies(response)
    _prevent_caching(response)
    return AuthenticationResponse(message="Logged out successfully.")


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Return current authenticated user profile",
)
def me(
    response: Response,
    current_user: User = Depends(require_authenticated_user),
) -> UserResponse:
    """Return the current profile without exposing authentication secrets."""
    _prevent_caching(response)
    return UserResponse.model_validate(current_user)


# ---------------------------------------------------------------------------
# Password Reset / Self-Service Recovery Routes
# ---------------------------------------------------------------------------

from app.schemas.auth import (
    PasswordResetConfirmRequest,
    PasswordResetConfirmResponse,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    PasswordResetResendRequest,
    PasswordResetVerifyRequest,
    PasswordResetVerifyResponse,
)
from app.services.password_recovery import (
    InvalidRecoveryCodeError,
    InvalidResetAuthorizationError,
    RateLimitError,
    RecoveryAttemptLimitError,
    WeakPasswordError as RecoveryWeakPasswordError,
    confirm_password_reset,
    request_password_recovery,
    resend_password_recovery_code,
    verify_password_recovery_code,
)


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    status_code=status.HTTP_200_OK,
    summary="Request a password recovery code via email",
)
def password_reset_request(
    data: PasswordResetRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetRequestResponse:
    """
    Initiate a password recovery request.
    Always returns a generic, non-enumerating message and recovery request ID.
    """
    _prevent_caching(response)
    message, request_id = request_password_recovery(
        db,
        email=str(data.email),
        portal=data.portal,
    )
    return PasswordResetRequestResponse(
        message=message,
        recovery_request_id=request_id,
    )


@router.post(
    "/password-reset/verify",
    response_model=PasswordResetVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify the 8-digit email recovery code",
)
def password_reset_verify(
    data: PasswordResetVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetVerifyResponse:
    """
    Validate the 8-digit verification code.
    On success, returns a single-purpose, short-lived reset authorization token.
    """
    _prevent_caching(response)
    try:
        reset_token = verify_password_recovery_code(
            db,
            recovery_request_id=data.recovery_request_id,
            code=data.code,
        )
    except RecoveryAttemptLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except InvalidRecoveryCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return PasswordResetVerifyResponse(
        reset_authorization=reset_token,
    )


@router.post(
    "/password-reset/resend",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend verification code for an existing recovery request",
)
def password_reset_resend(
    data: PasswordResetResendRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthenticationResponse:
    """Resend recovery code, subject to cooldown limits."""
    _prevent_caching(response)
    try:
        msg = resend_password_recovery_code(
            db,
            recovery_request_id=data.recovery_request_id,
        )
    except RateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    return AuthenticationResponse(message=msg)


@router.post(
    "/password-reset/confirm",
    response_model=PasswordResetConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Set new password using single-use reset authorization",
)
def password_reset_confirm(
    data: PasswordResetConfirmRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetConfirmResponse:
    """
    Apply new password, atomically consume reset authorization,
    invalidate existing login sessions, and send confirmation email.
    """
    _prevent_caching(response)
    try:
        msg = confirm_password_reset(
            db,
            reset_authorization=data.reset_authorization,
            new_password=data.new_password,
            confirm_password=data.confirm_password,
        )
    except RecoveryWeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except InvalidResetAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return PasswordResetConfirmResponse(message=msg)
