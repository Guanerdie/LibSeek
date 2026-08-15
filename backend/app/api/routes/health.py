from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.api.dependencies import DbSession
from app.core.config import get_settings
from app.core.time import utc_now
from app.models.entities import WorkerHeartbeat
from app.schemas.entities import ComponentStatus, SystemStatusResponse
from app.workers.identity import JOB_WORKER_HEARTBEAT_PREFIX

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(session: DbSession) -> SystemStatusResponse:
    settings = get_settings()
    now = utc_now()
    postgres_healthy = True
    postgres_message = "PostgreSQL 连接正常"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        postgres_healthy = False
        postgres_message = "PostgreSQL 连接失败"

    worker_healthy = False
    worker_message = "尚未收到 Worker 心跳"
    if postgres_healthy:
        try:
            latest = await session.scalar(
                select(func.max(WorkerHeartbeat.last_seen_at)).where(
                    WorkerHeartbeat.worker_id.like(
                        f"{JOB_WORKER_HEARTBEAT_PREFIX}%"
                    )
                )
            )
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=now.tzinfo)
                age = now - latest
                worker_healthy = age <= timedelta(seconds=settings.worker_heartbeat_stale_seconds)
                worker_message = "Worker 运行正常" if worker_healthy else "Worker 心跳已过期"
        except Exception:
            worker_message = "无法读取 Worker 心跳"

    return SystemStatusResponse(
        api=ComponentStatus(healthy=True, message="API 运行正常", checked_at=now),
        worker=ComponentStatus(healthy=worker_healthy, message=worker_message, checked_at=now),
        postgres=ComponentStatus(
            healthy=postgres_healthy, message=postgres_message, checked_at=now
        ),
        nextfind_configured=settings.nextfind_configured,
        tmdb_configured=settings.tmdb_configured,
        tmdb_live_enabled=settings.enable_tmdb_live,
        pt_site_architecture=settings.pt_site_architecture,
        pt_site_label=settings.pt_site_display_name,
        pt_site_configured=settings.pt_site_configured,
        pt_site_runtime_supported=settings.pt_site_runtime_supported,
        pt_site_search_ready=settings.pt_site_search_ready,
        pt_site_status=_pt_site_status(settings),
        avistaz_configured=settings.avistaz_configured,
        avistaz_live_enabled=settings.enable_avistaz_live_search,
        avistaz_status=(
            "只读搜索已启用"
            if settings.enable_avistaz_live_search and settings.avistaz_configured
            else "只读搜索默认关闭"
        ),
        qb_configured=settings.qb_configured,
        qb_read_only_enabled=settings.enable_qb_read_only,
        qb_status=(
            "只读预检已启用"
            if settings.enable_qb_read_only and settings.qb_configured
            else "只读连接默认关闭"
        ),
        download_control_plane_enabled=(
            settings.enable_download_execution_control_plane
        ),
        download_executor_enabled=settings.enable_download_executor,
        avistaz_torrent_fetch_enabled=settings.enable_avistaz_torrent_fetch,
        qb_write_enabled=settings.enable_qb_write,
        download_monitor_enabled=settings.enable_download_monitor,
        automation_engine_enabled=settings.enable_automation_engine,
    )


def _pt_site_status(settings: object) -> str:
    if getattr(settings, "pt_site_runtime_supported", False) is not True:
        return "配置已保存，等待站点适配"
    if getattr(settings, "pt_site_search_ready", False) is True:
        return "只读搜索已启用"
    if getattr(settings, "pt_site_configured", False) is True:
        return "已配置，实时搜索保持关闭"
    return "尚未完成配置"
