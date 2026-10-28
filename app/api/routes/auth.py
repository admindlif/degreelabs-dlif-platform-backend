"""Authentication and Fellow onboarding routes."""

from io import BytesIO
from datetime import datetime, timezone

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_cookies import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    TWO_FA_CHALLENGE_COOKIE_NAME,
    clear_access_cookie,
    clear_all_auth_cookies,
    clear_mfa_enrollment_cookie,
    clear_onboarding_cookie,
    clear_refresh_cookie,
    clear_two_fa_challenge_cookie,
    set_access_cookie,
    set_mfa_enrollment_cookie,
    set_onboarding_cookie,
    set_refresh_cookie,
    set_two_fa_challenge_cookie,
)
from app.core.permissions import (
    require_2fa_setup_user,
    require_authenticated_session,
    require_authenticated_user,
)
from app.core.security import (
    create_mfa_enrollment_token,
    create_onboarding_token,
    decode_access_token,
    get_totp_uri,
)
from app.db.session import get_db
from app.models.auth_session import AuthSession
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
    SessionActivityResponse,
    SessionRefreshResponse,
    SessionStatusResponse,
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
from app.services.auth_rate_limit import (
    AuthRateLimitExceeded,
    RateLimitPolicy,
    clear_rate_limit_failures,
    enforce_rate_limit,
    login_policy,
    recovery_policy,
    refresh_policy,
    register_rate_limit_failure,
    two_fa_policy,
)
from app.services.auth_session import (
    AuthSessionError,
    ConcurrentRefreshError,
    InvalidRefreshTokenError,
    RefreshTokenReuseError,
    RequestMetadata,
    SessionExpiredError,
    SessionRevokedError,
    add_audit_event,
    create_authenticated_session,
    ensure_session_active,
    get_session_from_access_token,
    get_session_from_refresh_token,
    hash_client_identifier,
    record_genuine_activity,
    refresh_authenticated_session,
    revoke_session,
    sanitize_user_agent,
    session_deadlines,
    utc_now,
)


router = APIRouter(prefix="/auth", tags=["Authentication"])


def _prevent_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _request_metadata(request: Request) -> RequestMetadata:
    client_host = request.client.host if request.client else None
    return RequestMetadata(
        ip_address_hash=hash_client_identifier(client_host),
        user_agent=sanitize_user_agent(request.headers.get("user-agent")),
    )


def _rate_identifiers(request: Request, subject: str | None = None) -> list[str]:
    client_host = request.client.host if request.client else "unknown"
    values = [f"ip:{client_host}"]
    if subject:
        values.append(f"subject:{subject.strip().lower()}")
    return values


def _enforce_rate_limit(
    db: Session,
    action: str,
    identifiers: list[str],
    policy: RateLimitPolicy,
) -> None:
    try:
        enforce_rate_limit(db, action, identifiers, policy=policy)
    except AuthRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _set_authenticated_session_cookies(response: Response, tokens) -> None:
    set_access_cookie(response, tokens.access_token)
    set_refresh_cookie(
        response,
        tokens.refresh_token,
        max_age=tokens.refresh_cookie_max_age,
    )


def _cleared_auth_error_response(
    detail: str,
    *,
    status_code: int = status.HTTP_401_UNAUTHORIZED,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )
    clear_all_auth_cookies(response)
    _prevent_caching(response)
    return response


@router.post(
    "/activate",
    response_model=ActivateAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate account with invitation token",
)
def activate(
    data: ActivateAccountRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> ActivateAccountResponse:
    """Set the invited Fellow's password and begin the 2FA setup session."""
    identifiers = _rate_identifiers(request, data.token)
    _enforce_rate_limit(db, "activate", identifiers, login_policy())
    try:
        user = activate_account(db, data)
    except (InvalidInvitationError, AccountNotInvitedError) as exc:
        register_rate_limit_failure(
            db, "activate", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except WeakPasswordError as exc:
        register_rate_limit_failure(
            db, "activate", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "activate", identifiers[1:])
    set_onboarding_cookie(response, create_onboarding_token(str(user.id)))
    clear_access_cookie(response)
    clear_refresh_cookie(response)
    clear_mfa_enrollment_cookie(response)
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
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> OnboardingResumeResponse:
    identifiers = _rate_identifiers(request, str(data.email))
    _enforce_rate_limit(db, "onboarding_resume", identifiers, login_policy())
    try:
        user = resume_onboarding(db, str(data.email), data.password)
    except InvalidCredentialsError as exc:
        register_rate_limit_failure(
            db,
            "onboarding_resume",
            identifiers,
            policy=login_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resume onboarding with the supplied credentials.",
        ) from exc

    clear_rate_limit_failures(db, "onboarding_resume", identifiers[1:])
    set_onboarding_cookie(response, create_onboarding_token(str(user.id)))
    clear_access_cookie(response)
    clear_refresh_cookie(response)
    clear_mfa_enrollment_cookie(response)
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
    current_user: User = Depends(require_2fa_setup_user),
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
    current_user: User = Depends(require_2fa_setup_user),
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
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_2fa_setup_user),
) -> ConfirmTwoFAResponse:
    identifiers = _rate_identifiers(request, str(current_user.id))
    _enforce_rate_limit(db, "two_fa_enrollment", identifiers, two_fa_policy())
    try:
        plain_codes = confirm_2fa(db, current_user, data.code)
    except TwoFANotConfiguredError as exc:
        register_rate_limit_failure(
            db,
            "two_fa_enrollment",
            identifiers,
            policy=two_fa_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except InvalidTwoFACodeError as exc:
        register_rate_limit_failure(
            db,
            "two_fa_enrollment",
            identifiers,
            policy=two_fa_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "two_fa_enrollment", identifiers[1:])
    tokens = create_authenticated_session(
        db,
        current_user,
        metadata=_request_metadata(request),
    )
    _set_authenticated_session_cookies(response, tokens)
    clear_onboarding_cookie(response)
    clear_two_fa_challenge_cookie(response)
    clear_mfa_enrollment_cookie(response)
    _prevent_caching(response)

    return ConfirmTwoFAResponse(
        message="Two-factor authentication enabled.",
        recovery_codes=plain_codes,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    response_model_exclude_defaults=True,
    status_code=status.HTTP_200_OK,
    summary="Stage 1 login: verify password",
)
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Validate credentials and establish the next session cookie."""
    identifiers = _rate_identifiers(request, str(data.email))
    _enforce_rate_limit(db, "login", identifiers, login_policy())
    try:
        result = verify_login_credentials(
            db,
            str(data.email),
            data.password,
        )
    except InvalidCredentialsError as exc:
        add_audit_event(
            db,
            "login_failure",
            success=False,
            reason="invalid_credentials",
            metadata=_request_metadata(request),
        )
        register_rate_limit_failure(
            db, "login", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        ) from exc
    except AccountSuspendedError as exc:
        add_audit_event(
            db,
            "login_failure",
            success=False,
            reason="account_suspended",
            metadata=_request_metadata(request),
        )
        register_rate_limit_failure(
            db, "login", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountNotInvitedError as exc:
        register_rate_limit_failure(
            db, "login", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except TwoFANotConfiguredError as exc:
        register_rate_limit_failure(
            db, "login", identifiers, policy=login_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "login", identifiers[1:])
    clear_onboarding_cookie(response)
    _prevent_caching(response)

    if result.authenticated_without_mfa:
        tokens = create_authenticated_session(
            db,
            result.user,
            metadata=_request_metadata(request),
        )
        _set_authenticated_session_cookies(response, tokens)
        clear_two_fa_challenge_cookie(response)
        clear_mfa_enrollment_cookie(response)
        return LoginResponse(requires_2fa=False, requires_2fa_setup=False)

    if result.requires_mfa_enrollment:
        clear_access_cookie(response)
        clear_refresh_cookie(response)
        clear_two_fa_challenge_cookie(response)
        set_mfa_enrollment_cookie(
            response,
            create_mfa_enrollment_token(str(result.user.id)),
        )
        add_audit_event(
            db,
            "mfa_enrollment_required",
            user_id=result.user.id,
            metadata=_request_metadata(request),
        )
        db.commit()
        return LoginResponse(requires_2fa=False, requires_2fa_setup=True)

    if result.challenge_token is None:
        raise RuntimeError("Authentication service did not issue a session token.")

    clear_access_cookie(response)
    clear_refresh_cookie(response)
    clear_mfa_enrollment_cookie(response)
    set_two_fa_challenge_cookie(response, result.challenge_token)
    return LoginResponse(requires_2fa=True, requires_2fa_setup=False)


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

    identifiers = _rate_identifiers(request, challenge_token)
    _enforce_rate_limit(db, "two_fa_login", identifiers, two_fa_policy())

    try:
        user = complete_2fa_login(
            db,
            challenge_token,
            data.code,
        )
    except InvalidCredentialsError as exc:
        register_rate_limit_failure(
            db, "two_fa_login", identifiers, policy=two_fa_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except InvalidTwoFACodeError as exc:
        register_rate_limit_failure(
            db, "two_fa_login", identifiers, policy=two_fa_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication code.",
        ) from exc
    except AccountSuspendedError as exc:
        register_rate_limit_failure(
            db, "two_fa_login", identifiers, policy=two_fa_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except TwoFANotConfiguredError as exc:
        register_rate_limit_failure(
            db, "two_fa_login", identifiers, policy=two_fa_policy()
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "two_fa_login", identifiers[1:])
    tokens = create_authenticated_session(
        db,
        user,
        metadata=_request_metadata(request),
    )
    _set_authenticated_session_cookies(response, tokens)
    clear_two_fa_challenge_cookie(response)
    clear_mfa_enrollment_cookie(response)
    _prevent_caching(response)
    return AuthenticationResponse(message="Authentication successful.")


@router.post(
    "/logout",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke the current authentication session",
)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthenticationResponse:
    auth_session = None
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    access_token = request.cookies.get(ACCESS_COOKIE_NAME)
    if refresh_token:
        auth_session = get_session_from_refresh_token(db, refresh_token)
    if auth_session is None and access_token:
        auth_session = get_session_from_access_token(db, access_token)
    if auth_session is not None:
        revoke_session(
            db,
            auth_session,
            reason="user_logout",
            metadata=_request_metadata(request),
            audit_event_type="logout",
        )
        db.commit()
    clear_all_auth_cookies(response)
    _prevent_caching(response)
    return AuthenticationResponse(message="Logged out successfully.")


@router.post(
    "/refresh",
    response_model=SessionRefreshResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate the refresh token and issue a new access token",
)
def refresh_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionRefreshResponse | Response:
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_refresh_token is None:
        return _cleared_auth_error_response(
            "Refresh authentication is required."
        )

    identifiers = _rate_identifiers(request, raw_refresh_token)
    _enforce_rate_limit(db, "refresh", identifiers, refresh_policy())
    try:
        tokens = refresh_authenticated_session(
            db,
            raw_refresh_token,
            metadata=_request_metadata(request),
        )
    except ConcurrentRefreshError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except (
        InvalidRefreshTokenError,
        RefreshTokenReuseError,
        SessionExpiredError,
        SessionRevokedError,
    ) as exc:
        register_rate_limit_failure(
            db,
            "refresh",
            identifiers,
            policy=refresh_policy(),
        )
        return _cleared_auth_error_response(str(exc))

    clear_rate_limit_failures(db, "refresh", identifiers[1:])
    _set_authenticated_session_cookies(response, tokens)
    _prevent_caching(response)
    return SessionRefreshResponse()


@router.get(
    "/session",
    response_model=SessionStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Return non-sensitive server-authoritative session status",
)
def session_status(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SessionStatusResponse:
    now = utc_now()
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    access_token = request.cookies.get(ACCESS_COOKIE_NAME)
    auth_session = None
    if raw_refresh_token:
        auth_session = get_session_from_refresh_token(db, raw_refresh_token)
    if auth_session is None and access_token:
        try:
            decode_access_token(access_token)
        except Exception:
            auth_session = None
        else:
            auth_session = get_session_from_access_token(db, access_token)

    if auth_session is None:
        _prevent_caching(response)
        return SessionStatusResponse(authenticated=False, server_time=now)

    user = db.get(User, auth_session.user_id)
    if user is None:
        clear_all_auth_cookies(response)
        return SessionStatusResponse(authenticated=False, server_time=now)

    try:
        deadlines = ensure_session_active(
            db,
            auth_session,
            user,
            metadata=_request_metadata(request),
            now=now,
        )
    except AuthSessionError:
        clear_all_auth_cookies(response)
        return SessionStatusResponse(authenticated=False, server_time=now)

    access_expires_at = None
    if access_token:
        try:
            payload = decode_access_token(access_token, verify_expiration=False)
            access_expires_at = datetime.fromtimestamp(
                float(payload["exp"]),
                tz=timezone.utc,
            )
        except Exception:
            access_expires_at = None

    _prevent_caching(response)
    return SessionStatusResponse(
        authenticated=True,
        inactivity_expires_at=deadlines.inactivity_expires_at,
        absolute_expires_at=deadlines.absolute_expires_at,
        effective_expires_at=deadlines.effective_expires_at,
        access_expires_at=access_expires_at,
        server_time=now,
    )


@router.post(
    "/activity",
    response_model=SessionActivityResponse,
    status_code=status.HTTP_200_OK,
    summary="Register authenticated genuine user activity",
)
def register_session_activity(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_authenticated_user),
    auth_session: AuthSession = Depends(require_authenticated_session),
) -> SessionActivityResponse | Response:
    now = utc_now()
    try:
        deadlines = record_genuine_activity(
            db,
            auth_session,
            current_user,
            metadata=_request_metadata(request),
            now=now,
        )
    except AuthSessionError as exc:
        return _cleared_auth_error_response(str(exc))

    _prevent_caching(response)
    return SessionActivityResponse(
        inactivity_expires_at=deadlines.inactivity_expires_at,
        absolute_expires_at=deadlines.absolute_expires_at,
        effective_expires_at=deadlines.effective_expires_at,
        server_time=now,
    )


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
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetRequestResponse:
    """
    Initiate a password recovery request.
    Always returns a generic, non-enumerating message and recovery request ID.
    """
    identifiers = _rate_identifiers(request, str(data.email))
    _enforce_rate_limit(
        db,
        "password_reset_request",
        identifiers,
        recovery_policy(),
    )
    _prevent_caching(response)
    message, request_id = request_password_recovery(
        db,
        email=str(data.email),
        portal=data.portal,
    )
    # Count requests even when the non-enumerating service returns success.
    register_rate_limit_failure(
        db,
        "password_reset_request",
        identifiers,
        policy=recovery_policy(),
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
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetVerifyResponse:
    """
    Validate the 8-digit verification code.
    On success, returns a single-purpose, short-lived reset authorization token.
    """
    identifiers = _rate_identifiers(request, str(data.recovery_request_id))
    _enforce_rate_limit(
        db,
        "password_reset_verify",
        identifiers,
        recovery_policy(),
    )
    _prevent_caching(response)
    try:
        reset_token = verify_password_recovery_code(
            db,
            recovery_request_id=data.recovery_request_id,
            code=data.code,
        )
    except RecoveryAttemptLimitError as exc:
        register_rate_limit_failure(
            db,
            "password_reset_verify",
            identifiers,
            policy=recovery_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except InvalidRecoveryCodeError as exc:
        register_rate_limit_failure(
            db,
            "password_reset_verify",
            identifiers,
            policy=recovery_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "password_reset_verify", identifiers[1:])
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
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthenticationResponse:
    """Resend recovery code, subject to cooldown limits."""
    identifiers = _rate_identifiers(request, str(data.recovery_request_id))
    _enforce_rate_limit(
        db,
        "password_reset_resend",
        identifiers,
        recovery_policy(),
    )
    _prevent_caching(response)
    try:
        msg = resend_password_recovery_code(
            db,
            recovery_request_id=data.recovery_request_id,
        )
    except RateLimitError as exc:
        register_rate_limit_failure(
            db,
            "password_reset_resend",
            identifiers,
            policy=recovery_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    register_rate_limit_failure(
        db,
        "password_reset_resend",
        identifiers,
        policy=recovery_policy(),
    )
    return AuthenticationResponse(message=msg)


@router.post(
    "/password-reset/confirm",
    response_model=PasswordResetConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Set new password using single-use reset authorization",
)
def password_reset_confirm(
    data: PasswordResetConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PasswordResetConfirmResponse:
    """
    Apply new password, atomically consume reset authorization,
    invalidate existing login sessions, and send confirmation email.
    """
    identifiers = _rate_identifiers(request, data.reset_authorization)
    _enforce_rate_limit(
        db,
        "password_reset_confirm",
        identifiers,
        recovery_policy(),
    )
    _prevent_caching(response)
    try:
        msg = confirm_password_reset(
            db,
            reset_authorization=data.reset_authorization,
            new_password=data.new_password,
            confirm_password=data.confirm_password,
        )
    except RecoveryWeakPasswordError as exc:
        register_rate_limit_failure(
            db,
            "password_reset_confirm",
            identifiers,
            policy=recovery_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except InvalidResetAuthorizationError as exc:
        register_rate_limit_failure(
            db,
            "password_reset_confirm",
            identifiers,
            policy=recovery_policy(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    clear_rate_limit_failures(db, "password_reset_confirm", identifiers[1:])
    return PasswordResetConfirmResponse(message=msg)
