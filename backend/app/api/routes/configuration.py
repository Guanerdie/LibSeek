from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Response

from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.adapters.media_sources import NextFindAdapter
from app.adapters.metadata import TmdbProvider
from app.adapters.pt_sites import AvistaZAdapter, NexusPhpConnectionProbe
from app.api.dependencies import AdminPrincipal, SettingsDep, ViewerPrincipal
from app.core.config import Settings, apply_runtime_configuration
from app.core.runtime_config import (
    RuntimeConfigError,
    RuntimeConfigValidationError,
    RuntimePtSite,
    runtime_store,
)
from app.core.security import validate_public_external_target
from app.db.session import SessionFactory
from app.errors import AppError
from app.models.enums import AuthRole
from app.schemas.adapters import ProbeResult
from app.schemas.configuration import (
    PT_SITE_ARCHITECTURES,
    AvistaZConfigurationResponse,
    ConfigurationResponse,
    ConfigurationTestResponse,
    ConfigurationUpdateRequest,
    NextFindConfigurationResponse,
    NexusPhpConfigurationResponse,
    PtSiteConfigurationResponse,
    PtSiteConfigurationsResponse,
    QbittorrentConfigurationResponse,
    TmdbConfigurationResponse,
)
from app.services.site_rate_limit import PostgresAdvisoryRequestGate

router = APIRouter(prefix="/configuration", tags=["configuration"])


@router.get("", response_model=ConfigurationResponse)
async def get_configuration(
    response: Response,
    settings: SettingsDep,
    principal: ViewerPrincipal,
) -> ConfigurationResponse:
    if principal.role != AuthRole.ADMIN:
        raise AppError(
            "AUTH_ROLE_FORBIDDEN",
            "当前账号角色无权查看配置",
            status_code=403,
            details={"required_role": AuthRole.ADMIN.value},
        )
    _no_store(response)
    try:
        effective = apply_runtime_configuration(settings)
    except RuntimeConfigError as exc:
        raise AppError(
            "CONFIGURATION_UNAVAILABLE",
            "本机配置暂时无法读取",
            status_code=503,
        ) from exc
    return _response(effective)


@router.put("", response_model=ConfigurationResponse)
async def update_configuration(
    request: ConfigurationUpdateRequest,
    response: Response,
    settings: SettingsDep,
    _principal: AdminPrincipal,
) -> ConfigurationResponse:
    store = runtime_store(settings)
    try:
        store.save_configuration(request.runtime_updates())
        effective = apply_runtime_configuration(settings)
    except RuntimeConfigValidationError as exc:
        raise AppError(
            "CONFIGURATION_INVALID",
            "配置字段无效，请检查地址和输入格式",
            status_code=422,
        ) from exc
    except RuntimeConfigError as exc:
        raise AppError(
            "CONFIGURATION_UNAVAILABLE",
            "本机配置暂时无法保存",
            status_code=503,
        ) from exc
    _no_store(response)
    return _response(effective)


@router.post("/tests/nextfind", response_model=ConfigurationTestResponse)
async def test_nextfind_configuration(
    response: Response,
    settings: SettingsDep,
    _principal: AdminPrincipal,
) -> ConfigurationTestResponse:
    _no_store(response)
    effective = _effective_settings(settings)
    credentials = effective.nextfind_credentials()
    if credentials is None:
        raise AppError("NEXTFIND_NOT_CONFIGURED", "请先保存完整的 NextFind 配置", status_code=409)
    username, password = credentials
    await validate_public_external_target(
        effective.nextfind_base_url,
        effective.nextfind_allowed_hosts,
        resolve_timeout=effective.external_connect_timeout_seconds,
    )
    adapter = NextFindAdapter(
        base_url=effective.nextfind_base_url,
        allowed_hosts=effective.nextfind_allowed_hosts,
        username=username,
        password=password,
        max_response_bytes=effective.external_max_response_bytes,
        max_line_bytes=effective.external_max_ndjson_line_bytes,
        connect_timeout=effective.external_connect_timeout_seconds,
        read_timeout=effective.external_read_timeout_seconds,
    )
    try:
        return _test_response("nextfind", await adapter.probe())
    finally:
        await adapter.aclose()


@router.post("/tests/tmdb", response_model=ConfigurationTestResponse)
async def test_tmdb_configuration(
    response: Response,
    settings: SettingsDep,
    _principal: AdminPrincipal,
) -> ConfigurationTestResponse:
    _no_store(response)
    effective = _effective_settings(settings)
    token = effective.tmdb_token_value()
    if not token:
        raise AppError("TMDB_NOT_CONFIGURED", "请先保存 TMDB Access Token", status_code=409)
    provider = TmdbProvider(
        access_token=token,
        base_url=effective.tmdb_base_url,
        allowed_hosts=("api.themoviedb.org",),
        connect_timeout=effective.external_connect_timeout_seconds,
        read_timeout=effective.external_read_timeout_seconds,
        max_response_bytes=effective.external_max_response_bytes,
        min_interval_seconds=effective.tmdb_min_interval_seconds,
    )
    try:
        return _test_response("tmdb", await provider.probe())
    finally:
        await provider.aclose()


@router.post("/tests/pt-sites/{architecture}", response_model=ConfigurationTestResponse)
async def test_pt_site_configuration(
    architecture: str,
    response: Response,
    settings: SettingsDep,
    _principal: AdminPrincipal,
) -> ConfigurationTestResponse:
    _no_store(response)
    if architecture not in {"avistaz", "nexusphp"}:
        raise AppError("PT_SITE_ARCHITECTURE_INVALID", "PT 站点架构无效", status_code=404)
    effective = _effective_settings(settings)
    runtime = runtime_store(settings).configuration()
    if architecture == "nexusphp":
        site = runtime.nexusphp_site
        if site is None or not site.configured or not site.cookie:
            raise AppError(
                "NEXUSPHP_NOT_CONFIGURED",
                "请先保存完整的 NexusPHP 站点地址和 Cookie",
                status_code=409,
            )
        host = (urlsplit(site.base_url).hostname or "").casefold()
        await validate_public_external_target(
            site.base_url,
            (host,),
            resolve_timeout=effective.external_connect_timeout_seconds,
        )
        request_gate = PostgresAdvisoryRequestGate(
            SessionFactory,
            site.site_id,
            cooldown_seconds=effective.avistaz_min_interval_seconds,
        )
        probe = NexusPhpConnectionProbe(
            site.base_url,
            allowed_hosts=(host,),
            cookie_header=site.cookie,
            connect_timeout=effective.external_connect_timeout_seconds,
            read_timeout=effective.external_read_timeout_seconds,
            max_response_bytes=min(effective.external_max_response_bytes, 2 * 1024 * 1024),
            request_gate=request_gate.limit,
        )
        try:
            return _test_response("pt_site", await probe.probe())
        finally:
            await probe.aclose()

    site = runtime.avistaz_site
    if site is None:
        credentials = effective.avistaz_credentials()
        if credentials is None:
            raise AppError("AVISTAZ_NOT_CONFIGURED", "请先保存完整的 AvistaZ 配置", status_code=409)
        username, password, pid = credentials
        base_url = effective.avistaz_base_url
    else:
        if not site.configured or not site.username or not site.password or not site.pid:
            raise AppError("AVISTAZ_NOT_CONFIGURED", "请先保存完整的 AvistaZ 配置", status_code=409)
        username, password, pid = site.username, site.password, site.pid
        base_url = site.base_url
    host = (urlsplit(base_url).hostname or "").casefold()
    await validate_public_external_target(
        base_url,
        (host,),
        resolve_timeout=effective.external_connect_timeout_seconds,
    )
    request_gate = PostgresAdvisoryRequestGate(
        SessionFactory,
        "avistaz",
        cooldown_seconds=effective.avistaz_min_interval_seconds,
    )
    adapter = AvistaZAdapter(
        username=username,
        password=password,
        pid=pid,
        base_url=base_url,
        allowed_hosts=(host,),
        connect_timeout=effective.external_connect_timeout_seconds,
        read_timeout=effective.external_read_timeout_seconds,
        max_response_bytes=effective.external_max_response_bytes,
        min_interval_seconds=0,
        request_gate=request_gate.limit,
        enable_torrent_fetch=False,
    )
    try:
        return _test_response("pt_site", await adapter.probe())
    finally:
        await adapter.aclose()


@router.post("/tests/qbittorrent", response_model=ConfigurationTestResponse)
async def test_qbittorrent_configuration(
    response: Response,
    settings: SettingsDep,
    _principal: AdminPrincipal,
) -> ConfigurationTestResponse:
    _no_store(response)
    effective = _effective_settings(settings)
    credentials = effective.qb_credentials()
    if credentials is None or not effective.qb_allowed_hosts:
        raise AppError("QB_NOT_CONFIGURED", "请先保存完整的 qBittorrent 配置", status_code=409)
    base_url, username, password = credentials
    adapter = QbittorrentReadOnlyAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=effective.qb_allowed_hosts,
        allow_insecure_http=effective.qb_allow_insecure_http,
        connect_timeout=effective.external_connect_timeout_seconds,
        read_timeout=effective.external_read_timeout_seconds,
        max_response_bytes=effective.external_max_response_bytes,
    )
    try:
        await adapter.authenticate()
        version = await adapter.get_version()
        web_api_version = await adapter.get_web_api_version()
        return ConfigurationTestResponse(
            target="qbittorrent",
            healthy=True,
            message=f"qBittorrent 连接成功（{version} / Web API {web_api_version}）",
        )
    except AppError as exc:
        return _test_response(
            "qbittorrent",
            ProbeResult(healthy=False, error_code=exc.error_code, message=exc.message),
        )
    finally:
        await adapter.aclose()


def _effective_settings(settings: Settings) -> Settings:
    try:
        return apply_runtime_configuration(settings)
    except RuntimeConfigError as exc:
        raise AppError(
            "CONFIGURATION_UNAVAILABLE",
            "本机配置暂时无法读取",
            status_code=503,
        ) from exc


def _test_response(
    target: Literal["nextfind", "tmdb", "pt_site", "qbittorrent"],
    result: ProbeResult,
) -> ConfigurationTestResponse:
    return ConfigurationTestResponse(
        target=target,
        healthy=result.healthy,
        error_code=result.error_code,
        message=result.message,
    )


def _response(settings: Settings) -> ConfigurationResponse:
    store = runtime_store(settings)
    try:
        runtime = store.configuration()
        complete = store.configuration_complete()
        nextfind = settings.nextfind_credentials()
        qb = settings.qb_credentials()
    except (OSError, RuntimeConfigError):
        runtime = None
        complete = False
        nextfind = None
        qb = None
    nextfind_password_configured = bool(nextfind and nextfind[1])
    active_pt_site = _pt_site_response(runtime.pt_site if runtime else None, settings)
    stored_avistaz = (
        _pt_site_response(runtime.avistaz_site, settings)
        if runtime and runtime.avistaz_site
        else None
    )
    stored_nexusphp = (
        _pt_site_response(runtime.nexusphp_site, settings)
        if runtime and runtime.nexusphp_site
        else None
    )
    return ConfigurationResponse(
        nextfind=NextFindConfigurationResponse(
            base_url=settings.nextfind_base_url,
            username=nextfind[0] if nextfind else _secret_value(settings.nextfind_username),
            password_configured=nextfind_password_configured,
            configured=bool(settings.nextfind_configured and settings.nextfind_base_url),
        ),
        tmdb=TmdbConfigurationResponse(configured=settings.tmdb_configured),
        pt_site=active_pt_site,
        pt_sites=PtSiteConfigurationsResponse(
            avistaz=(
                stored_avistaz
                if isinstance(stored_avistaz, AvistaZConfigurationResponse)
                else active_pt_site
                if isinstance(active_pt_site, AvistaZConfigurationResponse)
                else None
            ),
            nexusphp=(
                stored_nexusphp
                if isinstance(stored_nexusphp, NexusPhpConfigurationResponse)
                else active_pt_site
                if isinstance(active_pt_site, NexusPhpConfigurationResponse)
                else None
            ),
        ),
        pt_site_architectures=PT_SITE_ARCHITECTURES,
        qbittorrent=QbittorrentConfigurationResponse(
            url=qb[0] if qb else _secret_value(settings.qb_base_url),
            username=qb[1] if qb else _secret_value(settings.qb_username),
            save_path=settings.qb_target_save_path or "",
            category=settings.qb_target_category or "",
            configured=settings.qb_configured,
            allow_insecure_http=settings.qb_allow_insecure_http,
        ),
        configuration_complete=complete,
    )


def _pt_site_response(
    site: RuntimePtSite | None, settings: Settings
) -> PtSiteConfigurationResponse | None:
    if site is None:
        credentials = settings.avistaz_credentials()
        username, password, pid = credentials or ("", "", "")
        return AvistaZConfigurationResponse(
            base_url=settings.avistaz_base_url,
            username=username,
            password_configured=bool(password),
            pid_configured=bool(pid),
            configured=bool(credentials),
            search_ready=settings.pt_site_search_ready,
        )
    if site.architecture == "avistaz":
        return AvistaZConfigurationResponse(
            base_url=site.base_url,
            username=site.username or "",
            password_configured=bool(site.password),
            pid_configured=bool(site.pid),
            configured=site.configured,
            search_ready=settings.pt_site_search_ready,
        )
    return NexusPhpConfigurationResponse(
        site_id=site.site_id,
        display_name=site.display_name,
        base_url=site.base_url,
        profile_id=None,
        cookie_configured=bool(site.cookie),
        passkey_configured=bool(site.passkey),
        configured=site.configured,
    )


def _secret_value(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    if not callable(getter):
        return ""
    raw = getter()
    return raw if isinstance(raw, str) else ""


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
