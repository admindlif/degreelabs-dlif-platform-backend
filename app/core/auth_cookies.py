"""Centralized configuration for authentication session cookies."""

from fastapi import Response

from app.core.config import settings


ONBOARDING_COOKIE_NAME = "dlif_onboarding"
TWO_FA_CHALLENGE_COOKIE_NAME = "dlif_2fa_challenge"
ACCESS_COOKIE_NAME = "dlif_access"

_AUTH_COOKIE_PATH = "/api/v1/auth"
_ACCESS_COOKIE_PATH = "/api/v1"


def _set_auth_cookie(
    response: Response,
    *,
    name: str,
    value: str,
    max_age: int,
    path: str,
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        path=path,
        domain=settings.effective_auth_cookie_domain,
        secure=settings.effective_auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _delete_auth_cookie(
    response: Response,
    *,
    name: str,
    path: str,
) -> None:
    response.delete_cookie(
        key=name,
        path=path,
        domain=settings.effective_auth_cookie_domain,
        secure=settings.effective_auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def set_onboarding_cookie(response: Response, token: str) -> None:
    _set_auth_cookie(
        response,
        name=ONBOARDING_COOKIE_NAME,
        value=token,
        max_age=settings.onboarding_token_expire_minutes * 60,
        path=_AUTH_COOKIE_PATH,
    )


def clear_onboarding_cookie(response: Response) -> None:
    _delete_auth_cookie(
        response,
        name=ONBOARDING_COOKIE_NAME,
        path=_AUTH_COOKIE_PATH,
    )


def set_two_fa_challenge_cookie(response: Response, token: str) -> None:
    _set_auth_cookie(
        response,
        name=TWO_FA_CHALLENGE_COOKIE_NAME,
        value=token,
        max_age=settings.two_fa_challenge_expire_minutes * 60,
        path=_AUTH_COOKIE_PATH,
    )


def clear_two_fa_challenge_cookie(response: Response) -> None:
    _delete_auth_cookie(
        response,
        name=TWO_FA_CHALLENGE_COOKIE_NAME,
        path=_AUTH_COOKIE_PATH,
    )


def set_access_cookie(response: Response, token: str) -> None:
    _set_auth_cookie(
        response,
        name=ACCESS_COOKIE_NAME,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        path=_ACCESS_COOKIE_PATH,
    )


def clear_access_cookie(response: Response) -> None:
    _delete_auth_cookie(
        response,
        name=ACCESS_COOKIE_NAME,
        path=_ACCESS_COOKIE_PATH,
    )


def clear_all_auth_cookies(response: Response) -> None:
    clear_access_cookie(response)
    clear_onboarding_cookie(response)
    clear_two_fa_challenge_cookie(response)
