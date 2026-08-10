from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.core.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    Principal,
    parse_session_token,
    role_allows,
    validate_bootstrap_csrf,
    validate_session_csrf,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.errors import AppError
from app.models.enums import AuthRole

DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_auth_material(settings: Settings) -> tuple[str, str, str, AuthRole]:
    try:
        material = settings.auth_material()
    except OSError as exc:
        raise AppError(
            "AUTH_NOT_CONFIGURED",
            "本地 API 认证 Secret 无法读取",
            status_code=503,
        ) from exc
    if material is None:
        raise AppError(
            "AUTH_NOT_CONFIGURED",
            "本地 API 认证尚未配置",
            status_code=503,
        )
    return material


async def get_current_principal(request: Request, settings: SettingsDep) -> Principal:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise AppError("AUTH_REQUIRED", "需要登录后才能访问", status_code=401)
    username, _, signing_key, role = require_auth_material(settings)
    principal = parse_session_token(token, signing_key)
    if principal.username != username or principal.role != role:
        raise AppError(
            "AUTH_SESSION_STALE",
            "账号配置已变化，请重新登录",
            status_code=401,
        )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


async def get_viewer_principal(principal: CurrentPrincipal) -> Principal:
    return _require_role(principal, AuthRole.VIEWER)


async def get_operator_principal(
    request: Request,
    principal: CurrentPrincipal,
) -> Principal:
    _validate_request_csrf(request, principal)
    return _require_role(principal, AuthRole.OPERATOR)


async def get_admin_principal(
    request: Request,
    principal: CurrentPrincipal,
) -> Principal:
    _validate_request_csrf(request, principal)
    return _require_role(principal, AuthRole.ADMIN)


async def get_authenticated_mutation_principal(
    request: Request,
    principal: CurrentPrincipal,
) -> Principal:
    _validate_request_csrf(request, principal)
    return principal


async def require_bootstrap_csrf(request: Request, settings: SettingsDep) -> None:
    _, _, signing_key, _ = require_auth_material(settings)
    validate_bootstrap_csrf(
        cookie_token=request.cookies.get(CSRF_COOKIE_NAME),
        header_token=request.headers.get(CSRF_HEADER_NAME),
        signing_key=signing_key,
        ttl_seconds=settings.auth_bootstrap_csrf_ttl_seconds,
    )


def _validate_request_csrf(request: Request, principal: Principal) -> None:
    validate_session_csrf(
        cookie_token=request.cookies.get(CSRF_COOKIE_NAME),
        header_token=request.headers.get(CSRF_HEADER_NAME),
        principal=principal,
    )


def _require_role(principal: Principal, required: AuthRole) -> Principal:
    if not role_allows(principal.role, required):
        raise AppError(
            "AUTH_ROLE_FORBIDDEN",
            "当前账号角色无权执行此操作",
            status_code=403,
            details={"required_role": required.value},
        )
    return principal


ViewerPrincipal = Annotated[Principal, Depends(get_viewer_principal)]
OperatorPrincipal = Annotated[Principal, Depends(get_operator_principal)]
AdminPrincipal = Annotated[Principal, Depends(get_admin_principal)]
AuthenticatedMutationPrincipal = Annotated[
    Principal, Depends(get_authenticated_mutation_principal)
]


async def get_qb_adapter() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
    settings = get_settings()
    if not settings.enable_qb_read_only:
        raise AppError("QB_READ_ONLY_DISABLED", "qBittorrent 只读连接默认关闭", status_code=409)
    credentials = settings.qb_credentials()
    if credentials is None or not settings.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时 Secret 未配置", status_code=409)
    base_url, username, password = credentials
    adapter = QbittorrentReadOnlyAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


QbAdapter = Annotated[ReadOnlyDownloaderAdapter, Depends(get_qb_adapter)]
