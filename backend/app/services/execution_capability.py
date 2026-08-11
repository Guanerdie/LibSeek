from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import validate_external_url
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import WorkerHeartbeat
from app.services.preflight import is_allowed_save_path

_CAPABILITY_PREFIX = "download-executor-ready:"


def require_download_executor_ready_configuration(settings: Settings) -> None:
    missing_flags = [
        name
        for name, enabled in (
            (
                "ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE",
                settings.enable_download_execution_control_plane,
            ),
            ("ENABLE_DOWNLOAD_EXECUTOR", settings.enable_download_executor),
            ("ENABLE_AVISTAZ_TORRENT_FETCH", settings.enable_avistaz_torrent_fetch),
            ("ENABLE_QB_WRITE", settings.enable_qb_write),
            ("ENABLE_AVISTAZ_LIVE_SEARCH", settings.enable_avistaz_live_search),
        )
        if not enabled
    ]
    if missing_flags:
        raise AppError(
            "DOWNLOAD_EXECUTOR_NOT_READY",
            "下载执行器能力开关未完整启用",
            status_code=409,
            details={"missing_flags": missing_flags},
        )
    if not settings.avistaz_configured or not settings.qb_configured:
        raise AppError(
            "DOWNLOAD_EXECUTOR_NOT_READY",
            "下载执行器运行时凭据尚未完整配置",
            status_code=409,
        )
    if (
        not settings.qb_target_category
        or not settings.qb_target_save_path
        or not settings.qb_allowed_save_paths
        or not is_allowed_save_path(
            settings.qb_target_save_path, settings.qb_allowed_save_paths
        )
    ):
        raise AppError(
            "DOWNLOAD_EXECUTOR_NOT_READY",
            "下载执行器目标分类或保存路径策略无效",
            status_code=409,
        )
    validate_external_url(settings.avistaz_base_url, settings.allowed_external_hosts)
    base_url = settings.qb_base_url_value()
    parsed = urlparse(base_url or "")
    allowed_schemes = {"https", "http"} if settings.qb_allow_insecure_http else {"https"}
    if (
        parsed.scheme not in allowed_schemes
        or (parsed.hostname or "").casefold() not in settings.qb_allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(
            "DOWNLOAD_EXECUTOR_NOT_READY",
            "下载执行器 qBittorrent 目标地址不符合安全策略",
            status_code=409,
        )


def download_executor_capability_fingerprint(settings: Settings) -> str:
    descriptor: dict[str, object] = {
        "version": 1,
        "avistaz_base_url": settings.avistaz_base_url.rstrip("/"),
        "allowed_external_hosts": sorted(settings.allowed_external_hosts),
        "qb_base_url": (settings.qb_base_url_value() or "").rstrip("/"),
        "qb_allowed_hosts": sorted(settings.qb_allowed_hosts),
        "qb_allow_insecure_http": settings.qb_allow_insecure_http,
        "qb_target_category": settings.qb_target_category,
        "qb_target_save_path": settings.qb_target_save_path,
        "qb_save_path_ref": settings.qb_save_path_ref,
        "qb_allowed_save_paths": sorted(settings.qb_allowed_save_paths),
        "qb_plan_tags": list(settings.qb_plan_tags),
        "qb_target_instance_ref": settings.qb_target_instance_ref,
        "torrent_max_bytes": settings.torrent_max_bytes,
        "torrent_max_files": settings.torrent_max_files,
    }
    encoded = json.dumps(
        descriptor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def download_executor_ready_worker_id(settings: Settings, instance_id: str) -> str:
    return (
        f"{_CAPABILITY_PREFIX}{download_executor_capability_fingerprint(settings)}:"
        f"{instance_id.strip()}"
    )[:180]


async def publish_download_executor_ready(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    instance_id: str,
) -> None:
    require_download_executor_ready_configuration(settings)
    worker_id = download_executor_ready_worker_id(settings, instance_id)
    async with session_factory() as session:
        heartbeat = await session.get(WorkerHeartbeat, worker_id)
        if heartbeat is None:
            session.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=utc_now()))
        else:
            heartbeat.last_seen_at = utc_now()
        await session.commit()


async def download_executor_is_ready(
    session: AsyncSession, settings: Settings
) -> bool:
    fingerprint = download_executor_capability_fingerprint(settings)
    prefix = f"{_CAPABILITY_PREFIX}{fingerprint}:"
    cutoff = utc_now() - timedelta(seconds=settings.download_executor_ready_ttl_seconds)
    heartbeat = await session.scalar(
        select(WorkerHeartbeat.last_seen_at)
        .where(
            WorkerHeartbeat.worker_id.like(f"{prefix}%"),
            WorkerHeartbeat.last_seen_at >= cutoff,
        )
        .order_by(WorkerHeartbeat.last_seen_at.desc())
        .limit(1)
    )
    return heartbeat is not None
