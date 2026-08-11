from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import WorkerHeartbeat
from app.services.preflight import is_allowed_save_path, preflight_policy_fingerprint

_CAPABILITY_PREFIX = "automation-preflight-ready:"


def require_automation_preflight_ready_configuration(settings: Settings) -> None:
    missing_flags = [
        name
        for name, enabled in (
            ("ENABLE_AUTOMATION_ENGINE", settings.enable_automation_engine),
            ("ENABLE_QB_READ_ONLY", settings.enable_qb_read_only),
        )
        if not enabled
    ]
    if missing_flags:
        raise AppError(
            "AUTOMATION_PREFLIGHT_NOT_READY",
            "自动预检能力开关未完整启用",
            status_code=409,
            details={"missing_flags": missing_flags},
        )
    if not settings.qb_configured:
        raise AppError(
            "AUTOMATION_PREFLIGHT_NOT_READY",
            "自动预检 qBittorrent 只读凭据尚未完整配置",
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
            "AUTOMATION_PREFLIGHT_NOT_READY",
            "自动预检目标分类或保存路径策略无效",
            status_code=409,
        )
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
            "AUTOMATION_PREFLIGHT_NOT_READY",
            "自动预检 qBittorrent 目标地址不符合安全策略",
            status_code=409,
        )


def automation_preflight_capability_fingerprint(settings: Settings) -> str:
    descriptor = {
        "version": 1,
        "preflight_policy_fingerprint": preflight_policy_fingerprint(settings),
        "qb_target_instance_ref": settings.qb_target_instance_ref,
    }
    encoded = json.dumps(
        descriptor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def automation_preflight_ready_worker_id(settings: Settings, instance_id: str) -> str:
    normalized_instance_id = instance_id.strip()
    if not normalized_instance_id:
        raise ValueError("automation preflight instance id must not be empty")
    return (
        f"{_CAPABILITY_PREFIX}"
        f"{automation_preflight_capability_fingerprint(settings)}:"
        f"{normalized_instance_id}"
    )[:180]


async def publish_automation_preflight_ready(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    instance_id: str,
) -> None:
    require_automation_preflight_ready_configuration(settings)
    worker_id = automation_preflight_ready_worker_id(settings, instance_id)
    async with session_factory() as session:
        heartbeat = await session.get(WorkerHeartbeat, worker_id)
        if heartbeat is None:
            session.add(WorkerHeartbeat(worker_id=worker_id, last_seen_at=utc_now()))
        else:
            heartbeat.last_seen_at = utc_now()
        await session.commit()


async def automation_preflight_is_ready(
    session: AsyncSession, settings: Settings
) -> bool:
    fingerprint = automation_preflight_capability_fingerprint(settings)
    prefix = f"{_CAPABILITY_PREFIX}{fingerprint}:"
    cutoff = utc_now() - timedelta(
        seconds=settings.automation_preflight_ready_ttl_seconds
    )
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
