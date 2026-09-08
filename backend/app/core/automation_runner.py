from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import PtSiteAdapter
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.session import SessionFactory
from app.errors import AppError
from app.services.rss_matcher import RssMatch, match_rss_to_library
from app.simple import automation
from app.simple.automation_state import refresh_automation_run_summary
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
    LibraryMediaItem,
)

_logger = logging.getLogger(__name__)
_manual_run_tasks: set[asyncio.Task[None]] = set()


async def recover_interrupted_jobs(session: AsyncSession) -> int:
    runs = list(
        await session.scalars(
            select(AutomationRun).where(
                AutomationRun.state.in_(
                    (AutomationRunState.PENDING, AutomationRunState.RUNNING)
                )
            )
        )
    )
    jobs = list(
        await session.scalars(
            select(AutomationJob).where(
                AutomationJob.state.in_(
                    (AutomationJobState.PENDING, AutomationJobState.RUNNING)
                ),
            )
        )
    )
    policy = await automation.get_policy(session)
    for job in jobs:
        await _recover_interrupted_job(
            session,
            job,
            max_attempts=policy.max_attempts,
            retry_message="上次执行被应用重启中断，等待重试",
        )
    for run in runs:
        refreshed = await refresh_automation_run_summary(session, run.id)
        if refreshed is None:
            continue
        if (
            refreshed.created_count == 0
            or refreshed.failed_count > 0
            or refreshed.deferred_count > 0
        ):
            refreshed.state = AutomationRunState.FAILED
            refreshed.error_message = "应用重启中断了本次自动化运行"
        else:
            refreshed.state = AutomationRunState.SUCCEEDED
            refreshed.error_message = None
        run.finished_at = utc_now()
    if jobs or runs:
        await session.commit()
    return len(jobs) + len(runs)


async def _recover_interrupted_job(
    session: AsyncSession,
    job: AutomationJob,
    *,
    max_attempts: int,
    retry_message: str,
) -> None:
    download = await session.get(Download, job.download_id) if job.download_id else None
    if download is not None and download.state == DownloadState.SUBMITTING:
        if download.submitted_at is None:
            download.state = DownloadState.ERROR
            download.error_message = "应用在 qBittorrent 写入前重启，等待安全重试"
            _defer_or_fail_job(
                job,
                max_attempts=max_attempts,
                retry_message=download.error_message,
                error_code="DOWNLOAD_NOT_SUBMITTED",
            )
        else:
            download.state = DownloadState.OUTCOME_UNKNOWN
            download.error_message = "应用在 qBittorrent 写入期间重启，请等待状态对账"
            _fail_job(job, download.error_message, error_code="QB_ADD_OUTCOME_UNKNOWN")
    elif download is not None and download.state == DownloadState.OUTCOME_UNKNOWN:
        _fail_job(
            job,
            download.error_message or "qBittorrent 写入结果未知",
            error_code="QB_ADD_OUTCOME_UNKNOWN",
        )
    elif download is not None and download.state in {
        DownloadState.QUEUED,
        DownloadState.DOWNLOADING,
        DownloadState.PAUSED,
        DownloadState.SEEDING,
        DownloadState.COMPLETED,
    }:
        job.state = AutomationJobState.SUCCEEDED
        job.error_code = None
        job.error_message = None
        job.next_attempt_at = None
        job.finished_at = utc_now()
    elif (
        download is not None
        and download.state == DownloadState.ERROR
        and job.state != AutomationJobState.PENDING
    ):
        _fail_job(
            job,
            download.error_message or "已有下载处于错误状态",
            error_code="DOWNLOAD_IN_ERROR_STATE",
        )
    else:
        _defer_or_fail_job(
            job,
            max_attempts=max_attempts,
            retry_message=retry_message,
            error_code="AUTOMATION_INTERRUPTED",
        )


def _defer_or_fail_job(
    job: AutomationJob, *, max_attempts: int, retry_message: str, error_code: str
) -> None:
    job.error_code = error_code
    job.error_message = retry_message
    job.finished_at = utc_now()
    if job.attempt_count < max_attempts:
        job.state = AutomationJobState.RETRY_WAIT
        job.next_attempt_at = utc_now()
    else:
        job.state = AutomationJobState.FAILED
        job.next_attempt_at = None


def _fail_job(job: AutomationJob, message: str, *, error_code: str) -> None:
    job.state = AutomationJobState.FAILED
    job.error_code = error_code
    job.error_message = message
    job.next_attempt_at = None
    job.finished_at = utc_now()


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
            await _fail_automation_run(session, run_id, message)
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
                await _fail_automation_run(session, run_id, message)
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


async def _fail_automation_run(
    session: AsyncSession, run_id: str, message: str
) -> bool:
    run = await session.get(AutomationRun, run_id)
    if run is None or run.state not in {
        AutomationRunState.PENDING,
        AutomationRunState.RUNNING,
    }:
        return False
    policy = await automation.get_policy(session)
    jobs = list(
        await session.scalars(
            select(AutomationJob).where(
                AutomationJob.run_id == run_id,
                AutomationJob.state.in_(
                    (AutomationJobState.PENDING, AutomationJobState.RUNNING)
                ),
            )
        )
    )
    for job in jobs:
        await _recover_interrupted_job(
            session,
            job,
            max_attempts=policy.max_attempts,
            retry_message=message,
        )
    refreshed = await refresh_automation_run_summary(session, run_id)
    if refreshed is None:
        return False
    refreshed.state = AutomationRunState.FAILED
    refreshed.error_message = message
    refreshed.finished_at = utc_now()
    await session.commit()
    return True


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


async def run_rss_match_cycle(
    session: AsyncSession,
    adapter: PtSiteAdapter,
    *,
    window_hours: int,
) -> list[RssMatch]:
    """Read the feed once and mark everything it matches as due for a search.

    The feed only produces leads, so a match does not download anything: it
    clears the media's search cooldown, which is what actually keeps a
    just-published release from sitting unnoticed until the next cooldown tier
    elapses.  The normal automation cycle then searches it properly.
    """

    fetch = getattr(adapter, "fetch_rss", None)
    if fetch is None:
        raise AppError(
            "PT_SITE_RSS_UNSUPPORTED",
            "该站点适配器不支持 RSS",
            status_code=409,
        )
    entries = await fetch(hours=window_hours)
    matches = await match_rss_to_library(session, entries)
    if not matches:
        return []
    now = utc_now()
    for match in matches:
        media = await session.get(LibraryMediaItem, match.media_id)
        if media is None:
            continue
        if media.next_search_at is not None and media.next_search_at > now:
            # The site says something new exists; the cooldown was a guess that
            # nothing would appear, and it has just been proven wrong.
            media.next_search_at = None
            media.search_miss_count = 0
    await session.commit()
    _logger.info("RSS matched %d library items", len(matches))
    return matches


async def rss_matcher_loop(
    stop: asyncio.Event,
    adapter_factory: Callable[[], PtSiteAdapter],
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    settings = get_settings()
    while not stop.is_set():
        adapter = None
        try:
            adapter = adapter_factory()
            async with session_factory() as session:
                await run_rss_match_cycle(
                    session, adapter, window_hours=settings.rss_matcher_window_hours
                )
        except Exception:
            # One bad cycle must not kill the loop; the next poll retries.
            _logger.exception("RSS match cycle failed")
        finally:
            if adapter is not None:
                await close_adapter(adapter)
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.rss_matcher_poll_seconds)
        except TimeoutError:
            continue
