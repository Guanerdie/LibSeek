from __future__ import annotations

import asyncio
import logging
import os
import socket
from urllib.parse import urlparse

from app.adapters.media_sources import NextFindAdapter
from app.adapters.metadata import TmdbProvider
from app.adapters.pt_sites import AvistaZAdapter
from app.adapters.pt_sites.catalog import build_pt_site_catalog
from app.adapters.pt_sites.registry import default_pt_site_registry
from app.core.config import Settings, get_settings
from app.core.security import validate_external_url
from app.db.session import SessionFactory
from app.errors import AppError
from app.services.site_rate_limit import PostgresAdvisoryRequestGate
from app.workers.identity import job_worker_id
from app.workers.processor import JobProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("unin.worker")


def build_nextfind_adapter(settings: Settings | None = None) -> NextFindAdapter:
    settings = settings or get_settings()
    validated_base_url = validate_external_url(
        settings.nextfind_base_url, settings.nextfind_allowed_hosts
    )
    nextfind_host = urlparse(validated_base_url).hostname
    if nextfind_host is None:
        raise AppError("INVALID_EXTERNAL_URL", "NextFind 地址格式无效", status_code=400)
    credentials = settings.nextfind_credentials()
    if credentials is None:
        raise AppError("NEXTFIND_NOT_CONFIGURED", "NextFind 尚未配置运行时凭据")
    username, password = credentials
    return NextFindAdapter(
        base_url=validated_base_url,
        allowed_hosts=(nextfind_host.lower(),),
        username=username,
        password=password,
        max_response_bytes=settings.external_max_response_bytes,
        max_line_bytes=settings.external_max_ndjson_line_bytes,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
    )


def build_tmdb_provider(settings: Settings | None = None) -> TmdbProvider:
    settings = settings or get_settings()
    if not settings.enable_tmdb_live:
        raise AppError("TMDB_LIVE_DISABLED", "TMDB 真实只读 Provider 默认关闭")
    token = settings.tmdb_token_value()
    if token is None:
        raise AppError("TMDB_NOT_CONFIGURED", "TMDB Access Token 未配置")
    if "api.themoviedb.org" not in settings.allowed_external_hosts:
        raise AppError("EXTERNAL_HOST_NOT_ALLOWED", "TMDB 域名不在外部访问白名单")
    return TmdbProvider(
        access_token=token,
        base_url=settings.tmdb_base_url,
        allowed_hosts=("api.themoviedb.org",),
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
        min_interval_seconds=settings.tmdb_min_interval_seconds,
        cache_ttl_seconds=settings.tmdb_cache_ttl_seconds,
        cache_max_entries=settings.tmdb_cache_max_entries,
        allow_future_episodes=settings.tmdb_allow_future_episodes,
    )


def build_avistaz_adapter(settings: Settings | None = None) -> AvistaZAdapter:
    settings = settings or get_settings()
    if settings.pt_site_architecture != "avistaz":
        raise AppError("PT_SITE_NOT_ACTIVE", "AvistaZ 不是当前活动 PT 站点")
    if not settings.enable_avistaz_live_search:
        raise AppError("AVISTAZ_LIVE_DISABLED", "AvistaZ 真实只读搜索默认关闭")
    credentials = settings.avistaz_credentials()
    if credentials is None:
        raise AppError("AVISTAZ_NOT_CONFIGURED", "AvistaZ 运行时凭据未配置")
    validated_base_url = validate_external_url(
        settings.avistaz_base_url, settings.avistaz_allowed_hosts
    )
    avistaz_host = urlparse(validated_base_url).hostname
    if avistaz_host is None:
        raise AppError("INVALID_EXTERNAL_URL", "AvistaZ 地址格式无效", status_code=400)
    username, password, pid = credentials
    request_gate = PostgresAdvisoryRequestGate(
        SessionFactory,
        "avistaz",
        cooldown_seconds=settings.avistaz_min_interval_seconds,
    )
    return AvistaZAdapter(
        username=username,
        password=password,
        pid=pid,
        base_url=validated_base_url,
        allowed_hosts=(avistaz_host.casefold(),),
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
        min_interval_seconds=settings.avistaz_min_interval_seconds,
        request_gate=request_gate.limit,
    )


def build_processor(settings: Settings, worker_id: str) -> JobProcessor:
    pt_site_catalog = build_pt_site_catalog(settings)
    pt_sites = default_pt_site_registry(
        lambda: build_avistaz_adapter(settings),
        catalog=pt_site_catalog,
    )
    return JobProcessor(
        SessionFactory,
        worker_id,
        lambda: build_nextfind_adapter(settings),
        lambda: build_tmdb_provider(settings),
        pt_site_registry=pt_sites,
        lease_seconds=settings.job_lease_seconds,
        lease_renew_interval_seconds=settings.job_lease_renew_interval_seconds,
        heartbeat_interval_seconds=min(
            10.0, settings.worker_heartbeat_stale_seconds / 3
        ),
        auto_enqueue_metadata_resolution=(
            settings.enable_tmdb_live and settings.tmdb_configured
        ),
    )


async def run() -> None:
    instance_id = os.getenv("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
    worker_id = job_worker_id(instance_id)
    logger.info("Worker started: %s", worker_id)
    while True:
        settings = get_settings()
        processor = build_processor(settings, worker_id)
        try:
            handled = await processor.run_once()
        except Exception:
            logger.exception("Worker polling cycle failed")
            handled = False
        if not handled:
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
