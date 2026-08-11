from __future__ import annotations

import asyncio
import logging
import os
import socket
from urllib.parse import urlparse

from app.adapters.downloaders import QbittorrentAdapter
from app.adapters.pt_sites import AvistaZAdapter
from app.core.config import Settings, get_settings
from app.core.security import validate_external_url
from app.db.session import SessionFactory
from app.errors import AppError
from app.services.site_rate_limit import PostgresAdvisoryRequestGate
from app.workers.download_executor import (
    DownloadExecutor,
    require_download_executor_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("unin.download-executor")


def build_avistaz_executor(settings: Settings) -> AvistaZAdapter:
    require_download_executor_enabled(settings)
    if not settings.enable_avistaz_live_search:
        raise AppError(
            "AVISTAZ_LIVE_DISABLED",
            "AvistaZ 真实搜索尚未启用，执行器无法重新绑定固定候选",
            status_code=403,
        )
    credentials = settings.avistaz_credentials()
    if credentials is None:
        raise AppError("AVISTAZ_NOT_CONFIGURED", "AvistaZ 运行时凭据未配置", status_code=409)
    validated_base_url = validate_external_url(
        settings.avistaz_base_url, settings.allowed_external_hosts
    )
    host = (urlparse(validated_base_url).hostname or "").casefold()
    if not host:
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
        allowed_hosts=(host,),
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.torrent_max_bytes,
        min_interval_seconds=settings.avistaz_min_interval_seconds,
        request_gate=request_gate.limit,
        enable_torrent_fetch=True,
    )


def build_qb_executor(settings: Settings) -> QbittorrentAdapter:
    require_download_executor_enabled(settings)
    credentials = settings.qb_credentials()
    if credentials is None:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时凭据未配置", status_code=409)
    base_url, username, password = credentials
    return QbittorrentAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        enable_write=True,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
    )


async def run() -> None:
    settings = get_settings()
    require_download_executor_enabled(settings)
    worker_id = os.getenv("DOWNLOAD_EXECUTOR_ID") or (
        f"download-executor:{socket.gethostname()}:{os.getpid()}"
    )
    executor = DownloadExecutor(
        SessionFactory,
        worker_id,
        lambda: build_avistaz_executor(settings),
        lambda: build_qb_executor(settings),
        settings,
    )
    logger.info("Download executor started: %s", worker_id)
    while True:
        try:
            handled = await executor.run_once()
        except AppError as exc:
            logger.error("Download executor cycle failed: error_code=%s", exc.error_code)
            handled = False
        except Exception as exc:
            logger.error(
                "Download executor cycle failed: exception_type=%s",
                type(exc).__name__,
            )
            handled = False
        if not handled:
            await asyncio.sleep(settings.download_executor_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
