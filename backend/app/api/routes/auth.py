from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import (
    AuthenticatedMutationPrincipal,
    SettingsDep,
    ViewerPrincipal,
    require_auth_material,
    require_bootstrap_csrf,
)
from app.core.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_bootstrap_csrf,
    create_session_token,
    new_csrf_token,
    parse_session_token,
    validate_session_csrf,
    verify_local_credentials,
)
from app.errors import AppError
from app.schemas.auth import CsrfResponse, LoginRequest, LoginResponse, PrincipalResponse

router = APIRouter(prefix="/auth", tags=["auth"])

BootstrapCsrf = Annotated[None, Depends(require_bootstrap_csrf)]


@router.get("/csrf", response_model=CsrfResponse)
async def get_csrf_token(
    request: Request,
    response: Response,
    settings: SettingsDep,
) -> CsrfResponse:
    username, _, signing_key, role = require_auth_material(settings)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    if session_token:
        try:
            principal = parse_session_token(session_token, signing_key)
            if principal.username != username or principal.role != role:
                raise AppError(
                    "AUTH_SESSION_STALE",
                    "账号配置已变化，请重新登录",
                    status_code=401,
                )
            validate_session_csrf(
                cookie_token=csrf_token,
                header_token=csrf_token,
                principal=principal,
            )
        except AppError:
            _clear_auth_cookies(response, settings.auth_cookie_secure)
        else:
            _no_store(response)
            return CsrfResponse(csrf_token=csrf_token or "")

    token = create_bootstrap_csrf(signing_key)
    _set_csrf_cookie(
        response,
        token,
        max_age=settings.auth_bootstrap_csrf_ttl_seconds,
        secure=settings.auth_cookie_secure,
    )
    _no_store(response)
    return CsrfResponse(csrf_token=token)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    response: Response,
    settings: SettingsDep,
    _csrf: BootstrapCsrf,
) -> LoginResponse:
    username, password, signing_key, role = require_auth_material(settings)
    if not verify_local_credentials(
        username=request.username,
        password=request.password.get_secret_value(),
        expected_username=username,
        expected_password=password,
    ):
        raise AppError("AUTH_LOGIN_FAILED", "用户名或密码错误", status_code=401)
    csrf_token = new_csrf_token()
    session_token = create_session_token(
        username=username,
        role=role,
        csrf_token=csrf_token,
        signing_key=signing_key,
        ttl_seconds=settings.auth_session_ttl_seconds,
    )
    _set_session_cookie(
        response,
        session_token,
        max_age=settings.auth_session_ttl_seconds,
        secure=settings.auth_cookie_secure,
    )
    _set_csrf_cookie(
        response,
        csrf_token,
        max_age=settings.auth_session_ttl_seconds,
        secure=settings.auth_cookie_secure,
    )
    _no_store(response)
    return LoginResponse(username=username, role=role, csrf_token=csrf_token)


@router.get("/me", response_model=PrincipalResponse)
async def current_user(response: Response, principal: ViewerPrincipal) -> PrincipalResponse:
    _no_store(response)
    return PrincipalResponse(username=principal.username, role=principal.role)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    settings: SettingsDep,
    _principal: AuthenticatedMutationPrincipal,
) -> Response:
    _clear_auth_cookies(response, settings.auth_cookie_secure)
    _no_store(response)
    response.status_code = 204
    return response


def _set_session_cookie(response: Response, token: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/api",
    )


def _set_csrf_cookie(response: Response, token: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/api",
    )


def _clear_auth_cookies(response: Response, secure: bool) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/api",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/api",
        secure=secure,
        httponly=False,
        samesite="strict",
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
