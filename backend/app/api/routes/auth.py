from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import (
    AuthenticatedMutationPrincipal,
    SettingsDep,
    ViewerPrincipal,
    legacy_auth_fields_present,
    require_auth_material,
    require_bootstrap_csrf,
)
from app.core.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthMaterial,
    create_bootstrap_csrf,
    create_password_digest,
    create_session_token,
    new_csrf_token,
    parse_session_token,
    validate_session_csrf,
    verify_auth_material_credentials,
)
from app.core.runtime_config import (
    RuntimeConfigAlreadyInitialized,
    RuntimeConfigError,
    runtime_store,
)
from app.errors import AppError
from app.models.enums import AuthRole
from app.schemas.auth import (
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    PrincipalResponse,
    SetupRequest,
    SetupStatusResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

BootstrapCsrf = Annotated[None, Depends(require_bootstrap_csrf)]


@router.get("/setup-status", response_model=SetupStatusResponse)
async def setup_status(response: Response, settings: SettingsDep) -> SetupStatusResponse:
    if legacy_auth_fields_present(settings):
        require_auth_material(settings)
        try:
            configuration_complete = runtime_store(settings).configuration_complete()
        except RuntimeConfigError as exc:
            raise _runtime_config_unavailable() from exc
        result = SetupStatusResponse(
            admin_initialized=True,
            configuration_complete=configuration_complete,
        )
        _no_store(response)
        return result
    try:
        status = runtime_store(settings).setup_status()
    except RuntimeConfigError as exc:
        raise _runtime_config_unavailable() from exc
    result = SetupStatusResponse(
        admin_initialized=status.admin_configured,
        configuration_complete=status.configuration_complete,
    )
    _no_store(response)
    return result


@router.post("/setup", response_model=LoginResponse)
async def setup(
    request: SetupRequest,
    response: Response,
    settings: SettingsDep,
) -> LoginResponse:
    if legacy_auth_fields_present(settings):
        require_auth_material(settings)
        raise _already_configured()
    try:
        store = runtime_store(settings)
        if store.setup_status().admin_configured:
            raise _already_configured()
        password_digest = create_password_digest(request.password.get_secret_value())
        record = store.setup_admin(request.username, password_digest)
    except RuntimeConfigAlreadyInitialized as exc:
        raise _already_configured() from exc
    except RuntimeConfigError as exc:
        raise _runtime_config_unavailable() from exc
    material = AuthMaterial(
        username=record.username,
        credential=record.password_digest,
        credential_kind="pbkdf2_sha256",
        signing_key=record.session_signing_key,
        role=AuthRole.ADMIN,
    )
    return _start_session(response, settings, material)


@router.get("/csrf", response_model=CsrfResponse)
async def get_csrf_token(
    request: Request,
    response: Response,
    settings: SettingsDep,
) -> CsrfResponse:
    material = require_auth_material(settings)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    if session_token:
        try:
            principal = parse_session_token(session_token, material.signing_key)
            if principal.username != material.username or principal.role != material.role:
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

    token = create_bootstrap_csrf(material.signing_key)
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
    material = require_auth_material(settings)
    if not verify_auth_material_credentials(
        username=request.username,
        password=request.password.get_secret_value(),
        material=material,
    ):
        raise AppError("AUTH_LOGIN_FAILED", "用户名或密码错误", status_code=401)
    return _start_session(response, settings, material)


def _start_session(
    response: Response,
    settings: SettingsDep,
    material: AuthMaterial,
) -> LoginResponse:
    csrf_token = new_csrf_token()
    session_token = create_session_token(
        username=material.username,
        role=material.role,
        csrf_token=csrf_token,
        signing_key=material.signing_key,
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
    return LoginResponse(
        username=material.username,
        role=material.role,
        csrf_token=csrf_token,
    )


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


def _already_configured() -> AppError:
    return AppError(
        "AUTH_ALREADY_CONFIGURED",
        "本地管理员账号已创建",
        status_code=409,
    )


def _runtime_config_unavailable() -> AppError:
    return AppError(
        "RUNTIME_CONFIG_UNAVAILABLE",
        "本机运行配置暂时无法读取",
        status_code=503,
    )
