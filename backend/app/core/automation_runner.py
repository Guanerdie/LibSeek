from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.errors import AppError
from app.simple import automation
from app.simple.integrations import (
    build_nextfind,
    build_pt_site,
    build_qb,
    build_qb_readonly,
    build_tmdb,
    close_adapter,
    sync_download_statuses,
    sync_nextfind,
)
from app.simple.models import (
    ActivityLog,
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    AutomationRunState,
    Download,
    DownloadState,
)

_logger = logging.getLogger(__name__)
_manual_run_tasks: set[asyncio.Task[None]] = set()


async def recover_interrupted_jobs(session: AsyncSession) -> int:
    jobs = list(
        await session.scalars(
            select(AutomationJob).where(AutomationJob.state == AutomationJobState.RUNNING)
        )
    )
    for job in jobs:
        download = await session.get(Download, job.download_id) if job.download_id else None
        if download is not None and download.state == DownloadState.SUBMITTING:
            if download.submitted_at is None:
                download.state = DownloadState.ERROR
                download.error_message = "应用在 qBittorrent 写入前重启，等待安全重试"
                job.state = AutomationJobState.RETRY_WAIT
                job.next_attempt_at = utc_now()
                job.error_message = download.error_message
            else:
                download.state = DownloadState.OUTCOME_UNKNOWN
                download.error_message = "应用在 qBittorrent 写入期间重启，请等待状态对账"
                job.state = AutomationJobState.FAILED
                job.next_attempt_at = None
                job.error_message = download.error_message
                job.finished_at = utc_now()
        elif download is not None and download.state == DownloadState.OUTCOME_UNKNOWN:
            job.state = AutomationJobState.FAILED
            job.next_attempt_at = None
            job.error_message = download.error_message or "qBittorrent 写入结果未知"
            job.finished_at = utc_now()
        elif download is not None and download.state in {
            DownloadState.QUEUED,
            DownloadState.DOWNLOADING,
            DownloadState.PAUSED,
            DownloadState.SEEDING,
            DownloadState.COMPLETED,
        }:
            job.state = AutomationJobState.SUCCEEDED
            job.next_attempt_at = None
            job.finished_at = utc_now()
        else:
            job.state = AutomationJobState.RETRY_WAIT
            job.next_attempt_at = utc_now()
            job.error_message = "上次执行被应用重启中断，等待重试"
    if jobs:
        await session.commit()
    runs = list(
        await session.scalars(
            select(AutomationRun).where(
                AutomationRun.state.in_(
                    (AutomationRunState.PENDING, AutomationRunState.RUNNING)
                )
            )
        )
    )
    for run in runs:
        run.state = AutomationRunState.FAILED
        run.error_message = "应用重启中断了本次自动化运行"
        run.finished_at = utc_now()
    if runs:
        await session.commit()
    return len(jobs) + len(runs)


async def execute_recorded_automation_run(
    session: AsyncSession,
    run: AutomationRun,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    run_id = run.id
    try:
        await automation.run_automation(
            session,
            adapter_factory=lambda site_id: build_pt_site(
                site_id, allow_torrent_fetch=False
            ),
            pt_factory=lambda site_id: build_pt_site(
                site_id, allow_torrent_fetch=True
            ),
            qb_factory=build_qb,
            metadata_factory=build_tmdb,
            trigger=run.trigger,
            stop_requested=stop_requested,
            run_record=run,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = exc.message if isinstance(exc, AppError) else "自动化运行失败"
        try:
            await session.rollback()
            stored = await session.get(AutomationRun, run_id)
            if stored is not None:
                stored.state = AutomationRunState.FAILED
                stored.error_message = message
                stored.finished_at = utc_now()
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as persist_exc:
            _logger.warning(
                "Unable to persist automation run failure with its original session",
                exc_info=persist_exc,
            )
            await _persist_automation_run_failure(run_id, message)
        if not isinstance(exc, AppError):
            _logger.exception("Unexpected automation run failure", exc_info=exc)


async def _execute_manual_automation_run(run_id: str) -> None:
    try:
        async with SessionFactory() as session:
            run = await session.get(AutomationRun, run_id)
            if run is None:
                return
            await execute_recorded_automation_run(session, run)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _logger.exception(
            "Manual automation run failed before execution was established",
            exc_info=exc,
        )
        message = exc.message if isinstance(exc, AppError) else "自动化后台任务启动失败"
        await _persist_automation_run_failure(run_id, message)


async def _persist_automation_run_failure(run_id: str, message: str) -> None:
    retry_delay = 1.0
    while True:
        try:
            async with SessionFactory() as session:
                run = await session.get(AutomationRun, run_id)
                if run is None or run.state not in {
                    AutomationRunState.PENDING,
                    AutomationRunState.RUNNING,
                }:
                    return
                run.state = AutomationRunState.FAILED
                run.error_message = message
                run.finished_at = utc_now()
                await session.commit()
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning(
                "Unable to persist automation run failure; retrying",
                exc_info=exc,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)


def _manual_run_done(task: asyncio.Task[None]) -> None:
    _manual_run_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        _logger.error(
            "Manual automation background task terminated unexpectedly",
            exc_info=exception,
        )


def queue_manual_automation_run(run_id: str) -> None:
    task = asyncio.create_task(_execute_manual_automation_run(run_id))
    _manual_run_tasks.add(task)
    task.add_done_callback(_manual_run_done)


async def cancel_manual_automation_runs() -> None:
    tasks = list(_manual_run_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_scheduled_cycle(
    session: AsyncSession, *, stop_requested: Callable[[], bool] | None = None
) -> bool:
    qb = None
    try:
        qb = build_qb_readonly()
        await sync_download_statuses(session, qb)
    except AppError:
        pass
    finally:
        if qb is not None:
            await close_adapter(qb)

    policy = await automation.get_policy(session)
    if not automation.policy_is_due(policy):
        return False

    nextfind = None
    try:
        nextfind = build_nextfind()
        await sync_nextfind(session, nextfind)
    except AppError as exc:
        session.add(
            ActivityLog(event="AUTOMATION_SYNC_FAILED", message=exc.message)
        )
        await session.commit()
    finally:
        if nextfind is not None:
            await close_adapter(nextfind)

    run = await automation.create_automation_run(session, trigger="scheduled")
    await execute_recorded_automation_run(
        session, run, stop_requested=stop_requested
    )

    return True


async def automation_scheduler_loop(
    stop: asyncio.Event,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    settings = get_settings()
    while not stop.is_set():
        try:
            async with session_factory() as session:
                await run_scheduled_cycle(session, stop_requested=stop.is_set)
        except Exception:
            # A failed cycle is retried by the next poll; job-level errors are persisted separately.
            _logger.exception("Automation scheduler cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.automation_scheduler_poll_seconds)
        except TimeoutError:
            continue
