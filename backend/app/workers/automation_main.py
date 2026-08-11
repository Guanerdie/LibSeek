from __future__ import annotations

import asyncio
import logging
import os
import socket

from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.core.config import Settings, get_settings
from app.db.session import SessionFactory
from app.errors import AppError
from app.services.automation_capability import (
    publish_automation_preflight_ready,
    require_automation_preflight_ready_configuration,
)
from app.workers.automation import (
    AutomationPreflightWorker,
    require_automation_preflight_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("unin.automation-preflight")


def build_qb_read_only(settings: Settings) -> QbittorrentReadOnlyAdapter:
    require_automation_preflight_enabled(settings)
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
    require_automation_preflight_enabled(settings)
    require_automation_preflight_ready_configuration(settings)
    configured_id = os.getenv("AUTOMATION_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
    worker = AutomationPreflightWorker(
        SessionFactory,
        f"automation-preflight:{configured_id}",
        lambda: build_qb_read_only(settings),
        settings,
    )
    ready_heartbeat = asyncio.create_task(
        _ready_heartbeat_loop(settings, configured_id)
    )
    logger.info("Automation preflight worker started")
    try:
        while True:
            try:
                handled = await worker.run_once()
            except AppError as exc:
                logger.error(
                    "Automation preflight cycle failed: error_code=%s", exc.error_code
                )
                handled = False
            except Exception as exc:
                logger.error(
                    "Automation preflight cycle failed: exception_type=%s",
                    type(exc).__name__,
                )
                handled = False
            if not handled:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        ready_heartbeat.cancel()
        try:
            await ready_heartbeat
        except asyncio.CancelledError:
            pass


async def _ready_heartbeat_loop(settings: Settings, instance_id: str) -> None:
    interval = max(5.0, settings.automation_preflight_ready_ttl_seconds / 3)
    while True:
        await publish_automation_preflight_ready(SessionFactory, settings, instance_id)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run())
