from __future__ import annotations

import asyncio
import logging
import os
import socket

from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.core.config import Settings, get_settings
from app.db.session import SessionFactory
from app.errors import AppError
from app.workers.download_executor import (
    DownloadMonitor,
    require_download_monitor_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("unin.download-monitor")


def build_qb_monitor(settings: Settings) -> QbittorrentReadOnlyAdapter:
    require_download_monitor_enabled(settings)
    credentials = settings.qb_credentials()
    if credentials is None:
        raise AppError("QB_NOT_CONFIGURED", "qBittorrent 运行时凭据未配置", status_code=409)
    base_url, username, password = credentials
    return QbittorrentReadOnlyAdapter(
        base_url=base_url,
        username=username,
        password=password,
        allowed_hosts=settings.qb_allowed_hosts,
        allow_insecure_http=settings.qb_allow_insecure_http,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
        max_response_bytes=settings.external_max_response_bytes,
    )


async def run() -> None:
    settings = get_settings()
    require_download_monitor_enabled(settings)
    monitor_id = os.getenv("DOWNLOAD_MONITOR_ID") or (
        f"download-monitor:{socket.gethostname()}:{os.getpid()}"
    )
    monitor = DownloadMonitor(
        SessionFactory,
        monitor_id,
        lambda: build_qb_monitor(settings),
        settings,
    )
    logger.info("Read-only download monitor started: %s", monitor_id)
    while True:
        try:
            await monitor.run_once()
        except AppError as exc:
            logger.error("Download monitor cycle failed: error_code=%s", exc.error_code)
        except Exception as exc:
            logger.error(
                "Download monitor cycle failed: exception_type=%s",
                type(exc).__name__,
            )
        await asyncio.sleep(settings.download_monitor_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run())
