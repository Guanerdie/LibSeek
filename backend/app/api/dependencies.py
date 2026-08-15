from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.adapters.pt_sites.catalog import PtSiteCatalog, build_pt_site_catalog
from app.core.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    AuthMaterial,
    Principal,
    parse_session_token,
    role_allows,
    validate_bootstrap_csrf,
    validate_session_csrf,
)
from app.core.config import Settings, get_settings
from app.core.runtime_config import RuntimeConfigError, runtime_store
from app.db.session import get_session
from app.errors import AppError
from app.models.enums import AuthRole

DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_pt_site_catalog(settings: SettingsDep) -> PtSiteCatalog:
    return build_pt_site_catalog(settings)


PtCatalog = Annotated[PtSiteCatalog, Depends(get_pt_site_catalog)]


def require_auth_material(settings: Settings) -> AuthMaterial:
    if legacy_auth_fields_present(settings):
        return _require_legacy_auth_material(settings)
    try:
        record = runtime_store(settings).auth_record()
    except RuntimeConfigError as exc:
        raise AppError(
            "AUTH_NOT_CONFIGURED",
            "本地认证配置无法读取",
            status_code=503,
        ) from exc
    if record is None:
        raise AppError(
            "AUTH_SETUP_REQUIRED",
            "请先创建本地管理员账号",
            status_code=409,
        )
    return AuthMaterial(
        username=record.username,
        credential=record.password_digest,
        credential_kind="pbkdf2_sha256",
        signing_key=record.session_signing_key,
        role=AuthRole.ADMIN,
    )


def legacy_auth_fields_present(settings: Settings) -> bool:
    secret_values = (
        settings.auth_local_username,
        settings.auth_local_password,
        settings.auth_session_signing_key,
    )
    if any(value is not None and bool(value.get_secret_value()) for value in secret_values):
        return True
    file_values = (
        settings.auth_local_username_file,
        settings.auth_local_password_file,
        settings.auth_session_signing_key_file,
    )
    # Compose represents an empty Path setting as '.', which is not a valid Secret file.
    return any(value is not None and str(value) not in {"", "."} for value in file_values)


def _require_legacy_auth_material(settings: Settings) -> AuthMaterial:
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
            "本地 API 认证配置不完整",
            status_code=503,
        )
    username, password, signing_key, role = material
    return AuthMaterial(
        username=username,
        credential=password,
        credential_kind="plaintext",
        signing_key=signing_key,
        role=role,
    )


async def get_current_principal(request: Request, settings: SettingsDep) -> Principal:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise AppError("AUTH_REQUIRED", "需要登录后才能访问", status_code=401)
    material = require_auth_material(settings)
    principal = parse_session_token(token, material.signing_key)
    if principal.username != material.username or principal.role != material.role:
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
    material = require_auth_material(settings)
    validate_bootstrap_csrf(
        cookie_token=request.cookies.get(CSRF_COOKIE_NAME),
        header_token=request.headers.get(CSRF_HEADER_NAME),
        signing_key=material.signing_key,
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
AuthenticatedMutationPrincipal = Annotated[Principal, Depends(get_authenticated_mutation_principal)]


@asynccontextmanager
async def open_qb_adapter(
    settings: Settings | None = None,
) -> AsyncIterator[ReadOnlyDownloaderAdapter]:
    effective_settings = settings or get_settings()
    if not effective_settings.enable_qb_read_only:
        raise AppError("QB_READ_ONLY_DISABLED", "qBittorrent 只读连接默认关闭", status_code=409)
    credentials = effective_settings.qb_credentials()
    if credentials is None or not effective_settings.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时 Secret 未配置", status_code=409)
    base_url, username, password = credentials
    adapter = QbittorrentReadOnlyAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=effective_settings.qb_allowed_hosts,
        allow_insecure_http=effective_settings.qb_allow_insecure_http,
        connect_timeout=effective_settings.external_connect_timeout_seconds,
        read_timeout=effective_settings.external_read_timeout_seconds,
        max_response_bytes=effective_settings.external_max_response_bytes,
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


async def get_qb_adapter() -> AsyncIterator[ReadOnlyDownloaderAdapter]:
    async with open_qb_adapter() as adapter:
        yield adapter


QbAdapter = Annotated[ReadOnlyDownloaderAdapter, Depends(get_qb_adapter)]
