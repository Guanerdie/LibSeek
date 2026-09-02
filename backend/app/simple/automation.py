from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import MetadataProvider, PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbittorrentAdapter
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.services.tv_pack import classify_tv_pack, tv_pack_rank
from app.simple.integrations import (
    close_adapter,
    identify_media,
    run_release_search,
    submit_download,
)
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    AutomationPolicy,
    AutomationRun,
    AutomationRunState,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
)
from app.simple.regions import NextFindRegion, nextfind_regions
from app.simple.schemas import AutomationPolicyUpdate
from app.simple.service import create_search, get_search

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_run_lock = asyncio.Lock()
_run_creation_lock = asyncio.Lock()
_logger = logging.getLogger(__name__)
_HARD_CORRECTNESS_WARNINGS = {
    "ID_MISMATCH",
    "ID_UNVERIFIED",
    "TV_PACK_UNVERIFIED",
    "YEAR_MISMATCH",
}
_EXACT_IDENTITY_REASONS = {"TMDB_ID_EXACT", "IMDB_ID_EXACT"}
_FALLBACK_IDENTITY_REASONS = {"TITLE_EXACT", "YEAR_MATCH", "MEDIA_TYPE_MATCH"}

# Search cooldown: after an automation search finds nothing acceptable, wait
# progressively longer before searching the same media item again.  Resources
# that do not exist on the site today rarely appear within hours, so repeating
# the same empty search every cycle only burns PT request quota.
_SEARCH_MISS_COOLDOWNS = (
    timedelta(days=1),
    timedelta(days=3),
    timedelta(days=7),
)


def _search_cooldown(miss_count: int) -> timedelta:
    if miss_count <= 0:
        return timedelta(0)
    index = min(miss_count, len(_SEARCH_MISS_COOLDOWNS)) - 1
    return _SEARCH_MISS_COOLDOWNS[index]


def _record_search_outcome(
    media: LibraryMediaItem, *, found_candidate: bool
) -> None:
    now = utc_now()
    media.last_searched_at = now
    if found_candidate:
        media.search_miss_count = 0
        media.next_search_at = None
    else:
        media.search_miss_count += 1
        media.next_search_at = now + _search_cooldown(media.search_miss_count)


async def get_policy(session: AsyncSession) -> AutomationPolicy:
    policy = await session.get(AutomationPolicy, "default")
    if policy is None:
        policy = AutomationPolicy(id="default")
        session.add(policy)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            policy = await session.get(AutomationPolicy, "default")
            if policy is None:
                raise
        await session.refresh(policy)
    return policy


async def update_policy(session: AsyncSession, payload: AutomationPolicyUpdate) -> AutomationPolicy:
    policy = await get_policy(session)
    policy.enabled = payload.enabled
    policy.dry_run = payload.dry_run
    policy.auto_identify = payload.auto_identify
    policy.scope_mode = payload.scope_mode
    policy.regions = [item.value for item in payload.regions]
    policy.selected_media_ids = payload.selected_media_ids
    policy.site_ids = payload.site_ids
    policy.media_types = [item.value for item in payload.media_types]
    policy.minimum_score = payload.minimum_score
    policy.minimum_seeders = payload.minimum_seeders
    policy.max_size_bytes = payload.max_size_bytes
    policy.allow_warnings = payload.allow_warnings
    policy.interval_minutes = payload.interval_minutes
    policy.retry_delay_minutes = payload.retry_delay_minutes
    policy.max_attempts = payload.max_attempts
    policy.daily_download_limit = payload.daily_download_limit
    policy.daily_download_bytes = payload.daily_download_bytes
    await session.commit()
    await session.refresh(policy)
    return policy


async def list_jobs(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    run_id: str | None = None,
) -> tuple[list[tuple[AutomationJob, str]], int]:
    """Return automation jobs in a stable, paginated order.

    ``run_id`` is optional to preserve the original global jobs endpoint.  A
    scoped query is used by the run-detail page so a long execution never
    expands the document into one giant list.
    """

    count_statement = select(func.count()).select_from(AutomationJob)
    jobs_statement = select(AutomationJob, LibraryMediaItem.title).join(
        LibraryMediaItem, LibraryMediaItem.id == AutomationJob.media_id
    )
    if run_id is not None:
        count_statement = count_statement.where(AutomationJob.run_id == run_id)
        jobs_statement = jobs_statement.where(AutomationJob.run_id == run_id)

    total = await session.scalar(count_statement)
    rows = await session.execute(
        jobs_statement.order_by(AutomationJob.created_at.desc(), AutomationJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows.tuples()), int(total or 0)


async def list_automation_runs(
    session: AsyncSession, *, page: int, page_size: int
) -> tuple[list[AutomationRun], int]:
    """Return execution history ordered newest first.

    Runs are persisted independently from their jobs, so this query remains
    useful while a run is pending (before any jobs have been created) and while
    its counters are being updated by the background worker.
    """

    total = await session.scalar(select(func.count()).select_from(AutomationRun))
    runs = list(
        await session.scalars(
            select(AutomationRun)
            .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return runs, int(total or 0)


async def list_run_jobs(
    session: AsyncSession,
    run_id: str,
    *,
    page: int,
    page_size: int,
) -> tuple[list[tuple[AutomationJob, str]], int]:
    """Return the jobs belonging to one execution run.

    The run existence check is intentionally performed by the route (which can
    return the established ``AUTOMATION_RUN_NOT_FOUND`` error).  Keeping this
    helper query-only also lets internal callers inspect orphaned jobs created
    by older databases if needed.
    """

    return await list_jobs(session, page=page, page_size=page_size, run_id=run_id)


async def create_automation_run(session: AsyncSession, *, trigger: str) -> AutomationRun:
    async with _run_creation_lock:
        policy = await get_policy(session)
        if not policy.enabled:
            raise AppError("AUTOMATION_DISABLED", "请先启用自动化策略", status_code=409)
        active = await session.scalar(
            select(AutomationRun)
            .where(
                AutomationRun.state.in_((AutomationRunState.PENDING, AutomationRunState.RUNNING))
            )
            .order_by(AutomationRun.created_at.desc())
            .limit(1)
        )
        if active is not None:
            raise AppError(
                "AUTOMATION_RUN_IN_PROGRESS",
                "已有自动化运行正在执行，请等待完成",
                status_code=409,
            )
        run = AutomationRun(trigger=trigger)
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def get_automation_run(session: AsyncSession, run_id: str) -> AutomationRun:
    run = await session.get(AutomationRun, run_id)
    if run is None:
        raise AppError("AUTOMATION_RUN_NOT_FOUND", "自动化运行不存在", status_code=404)
    return run


async def get_latest_automation_run(session: AsyncSession) -> AutomationRun | None:
    run: AutomationRun | None = await session.scalar(
        select(AutomationRun)
        .order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
        .limit(1)
    )
    return run


async def retry_job(session: AsyncSession, job_id: str) -> tuple[AutomationJob, AutomationRun]:
    """Create an immediately executable retry run without rewriting history."""

    async with _run_creation_lock:
        source = await session.get(AutomationJob, job_id)
        if source is None:
            raise AppError("AUTOMATION_JOB_NOT_FOUND", "自动化任务不存在", status_code=404)
        if (
            source.state
            not in {
                AutomationJobState.FAILED,
                AutomationJobState.RETRY_WAIT,
            }
            or source.superseded_at is not None
        ):
            raise AppError(
                "AUTOMATION_JOB_NOT_FAILED",
                "只有尚未重试的失败或等待任务可以重新执行",
                status_code=409,
            )

        policy = await get_policy(session)
        if not policy.enabled:
            raise AppError("AUTOMATION_DISABLED", "请先启用自动化策略", status_code=409)
        active = await session.scalar(
            select(AutomationRun)
            .where(
                AutomationRun.state.in_((AutomationRunState.PENDING, AutomationRunState.RUNNING))
            )
            .order_by(AutomationRun.created_at.desc())
            .limit(1)
        )
        if active is not None:
            raise AppError(
                "AUTOMATION_RUN_IN_PROGRESS",
                "已有自动化运行正在执行，请等待完成",
                status_code=409,
            )

        run = AutomationRun(trigger="manual_retry", created_count=1)
        session.add(run)
        await session.flush()
        retry = _copy_job_for_run(
            source,
            run_id=run.id,
            trigger="manual_retry",
            reset_attempts=True,
        )
        session.add(retry)
        _supersede_job(source)
        await session.commit()
        await session.refresh(retry)
        await session.refresh(run)
        return retry, run


def policy_is_due(policy: AutomationPolicy, now: datetime | None = None) -> bool:
    if not policy.enabled:
        return False
    now = now or utc_now()
    if policy.last_run_at is None:
        return True
    last_run = policy.last_run_at
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    return last_run + timedelta(minutes=policy.interval_minutes) <= now


async def run_automation(
    session: AsyncSession,
    *,
    adapter_factory: Callable[[str], PtSiteAdapter],
    pt_factory: Callable[[str], PtSiteAdapter] | None = None,
    qb_factory: Callable[[], QbittorrentAdapter] | None = None,
    metadata_factory: Callable[[], MetadataProvider] | None = None,
    trigger: str = "manual",
    limit: int = 20,
    stop_requested: Callable[[], bool] | None = None,
    run_record: AutomationRun | None = None,
) -> tuple[str, int, int, int]:
    async with _run_lock:
        policy = await get_policy(session)
        if not policy.enabled:
            raise AppError("AUTOMATION_DISABLED", "请先启用自动化策略", status_code=409)
        if not policy.dry_run and (pt_factory is None or qb_factory is None):
            raise AppError(
                "AUTOMATION_DOWNLOAD_NOT_AVAILABLE",
                "自动下载连接尚未准备好",
                status_code=409,
            )

        now = utc_now()
        run_id = run_record.id if run_record is not None else str(uuid4())
        retry_only = run_record is not None and run_record.trigger == "manual_retry"
        if run_record is not None:
            run_record.state = AutomationRunState.RUNNING
            run_record.started_at = now
            run_record.error_message = None
            await session.commit()
        if not retry_only:
            policy.last_run_at = now
        queued_statement = select(AutomationJob).where(
            AutomationJob.superseded_at.is_(None),
            (
                (AutomationJob.state == AutomationJobState.PENDING)
                | (
                    (AutomationJob.state == AutomationJobState.RETRY_WAIT)
                    & (AutomationJob.next_attempt_at <= now)
                )
            ),
        )
        if retry_only:
            # Clicking "retry" creates a run for exactly one copied job.  It
            # must not also pick up unrelated due jobs or start a fresh scan.
            queued_statement = queued_statement.where(AutomationJob.run_id == run_id)
        queued_sources = list(
            await session.scalars(queued_statement.order_by(AutomationJob.created_at).limit(limit))
        )
        queued_jobs: list[AutomationJob] = []
        for source in queued_sources:
            if run_record is None or source.run_id == run_id:
                queued_jobs.append(source)
                continue
            retry = _copy_job_for_run(
                source,
                run_id=run_id,
                trigger=trigger,
                reset_attempts=False,
            )
            session.add(retry)
            _supersede_job(source)
            queued_jobs.append(retry)
        retry_media_ids = {job.media_id for job in queued_jobs}
        unavailable_media_ids = select(AutomationJob.media_id).where(
            AutomationJob.superseded_at.is_(None),
            AutomationJob.state.in_(
                (
                    AutomationJobState.PENDING,
                    AutomationJobState.RUNNING,
                    AutomationJobState.RETRY_WAIT,
                    AutomationJobState.FAILED,
                )
            ),
        )
        remaining = 0 if retry_only else max(0, limit - len(queued_jobs))
        media_types = [MediaType(value) for value in policy.media_types]
        statement = (
            select(LibraryMediaItem)
            .where(
                LibraryMediaItem.media_type.in_(media_types),
                LibraryMediaItem.state.in_(
                    (
                        MediaState.READY,
                        MediaState.NEEDS_ATTENTION,
                        *(() if policy.dry_run else (MediaState.CANDIDATES,)),
                    )
                ),
                LibraryMediaItem.id.not_in(unavailable_media_ids),
                or_(
                    LibraryMediaItem.next_search_at.is_(None),
                    LibraryMediaItem.next_search_at <= now,
                ),
            )
            .order_by(LibraryMediaItem.updated_at.asc())
        )
        if not policy.auto_identify or metadata_factory is None:
            statement = statement.where(LibraryMediaItem.tmdb_id.is_not(None))
        if retry_media_ids:
            statement = statement.where(LibraryMediaItem.id.not_in(retry_media_ids))
        if policy.scope_mode == "selected":
            statement = statement.where(LibraryMediaItem.id.in_(policy.selected_media_ids))
        candidates = list(await session.scalars(statement)) if remaining else []
        media = [item for item in candidates if _media_in_policy_scope(policy, item)][:remaining]

        new_jobs = [
            AutomationJob(run_id=run_id, media_id=item.id, trigger=trigger) for item in media
        ]
        session.add_all(new_jobs)
        if run_record is not None:
            run_record.created_count = len([*queued_jobs, *new_jobs])
        await session.commit()

        succeeded = 0
        failed = 0
        deferred = 0
        jobs = [*queued_jobs, *new_jobs]
        for index, job in enumerate(jobs):
            if stop_requested is not None and stop_requested():
                deferred += len(jobs) - index
                break
            completed = await _execute_job(
                session,
                policy=policy,
                job=job,
                adapter_factory=adapter_factory,
                pt_factory=pt_factory,
                qb_factory=qb_factory,
                metadata_factory=metadata_factory,
            )
            if completed is True:
                succeeded += 1
            elif completed is False:
                failed += 1
            else:
                deferred += 1
            if run_record is not None:
                run_record.succeeded_count = succeeded
                run_record.failed_count = failed
                run_record.deferred_count = deferred
                await session.commit()
        if run_record is not None:
            run_record.succeeded_count = succeeded
            run_record.failed_count = failed
            run_record.deferred_count = deferred
            run_record.state = AutomationRunState.FAILED if failed else AutomationRunState.SUCCEEDED
            run_record.error_message = f"{failed} 个任务执行失败" if failed else None
            run_record.finished_at = utc_now()
            await session.commit()
        return run_id, len(jobs), succeeded, failed


async def run_dry_run(
    session: AsyncSession,
    *,
    adapter_factory: Callable[[str], PtSiteAdapter],
    limit: int = 20,
) -> tuple[str, int, int, int]:
    policy = await get_policy(session)
    if not policy.dry_run:
        raise AppError(
            "AUTOMATION_DRY_RUN_REQUIRED",
            "当前策略已启用真实下载模式",
            status_code=409,
        )
    return await run_automation(session, adapter_factory=adapter_factory, limit=limit)


async def _execute_job(
    session: AsyncSession,
    *,
    policy: AutomationPolicy,
    job: AutomationJob,
    adapter_factory: Callable[[str], PtSiteAdapter],
    pt_factory: Callable[[str], PtSiteAdapter] | None,
    qb_factory: Callable[[], QbittorrentAdapter] | None,
    metadata_factory: Callable[[], MetadataProvider] | None,
) -> bool | None:
    job_id = job.id
    policy_id = policy.id
    try:
        job.state = AutomationJobState.RUNNING
        job.attempt_count += 1
        job.next_attempt_at = None
        job.error_message = None
        job.finished_at = None
        await session.commit()

        media = await session.get(LibraryMediaItem, job.media_id)
        if media is None:
            raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
        if not _media_in_policy_scope(policy, media):
            job.decision = {
                "mode": "dry-run" if policy.dry_run else "live",
                "candidate_count": 0,
                "selected_title": None,
                "selected_score": None,
                "rejected": [
                    {
                        "candidate_id": None,
                        "title": media.title,
                        "reasons": ["影视已从当前自动化范围中移除"],
                    }
                ],
            }
            job.state = AutomationJobState.SUCCEEDED
            job.finished_at = utc_now()
            await session.commit()
            return True
        if media.tmdb_id is None:
            if not policy.auto_identify or metadata_factory is None:
                raise AppError(
                    "MEDIA_IDENTITY_REQUIRED",
                    "自动搜索前需要确认 TMDB 影视信息",
                    status_code=409,
                )
            provider = metadata_factory()
            try:
                await identify_media(session, media.id, provider)
            finally:
                await close_adapter(provider)

        selected = (
            await session.get(ReleaseCandidate, job.selected_candidate_id)
            if job.selected_candidate_id is not None
            else None
        )
        existing_download = (
            await session.get(Download, job.download_id) if job.download_id else None
        )
        if existing_download is None and selected is not None:
            existing_download = await session.scalar(
                select(Download).where(Download.candidate_id == selected.id)
            )
            if existing_download is not None:
                job.download_id = existing_download.id
        should_refresh_selection = selected is None or (
            existing_download is None or existing_download.state == DownloadState.ERROR
        )
        previous_selection = selected
        if should_refresh_selection:
            if media.media_type.value not in policy.media_types:
                selected = None
                job.decision = {
                    "mode": "dry-run" if policy.dry_run else "live",
                    "candidate_count": 0,
                    "selected_title": None,
                    "selected_score": None,
                    "rejected": [
                        {
                            "candidate_id": None,
                            "title": media.title,
                            "reasons": ["媒体类型已从当前策略中移除"],
                        }
                    ],
                }
                await session.commit()
            else:
                search = await create_search(
                    session, media_id=job.media_id, site_ids=policy.site_ids
                )
                job.search_id = search.id
                await session.commit()
                await run_release_search(
                    session,
                    search.id,
                    adapter_factory,
                    metadata_factory=metadata_factory,
                )
                _, candidates = await get_search(session, search.id)
                selected, rejected = _choose_candidate(
                    policy,
                    candidates,
                    media_type=media.media_type,
                    require_known_size=(
                        not policy.dry_run and policy.daily_download_bytes is not None
                    ),
                )
                if (
                    selected is not None
                    and previous_selection is not None
                    and existing_download is not None
                    and existing_download.state == DownloadState.ERROR
                    and _same_torrent(previous_selection, selected)
                ):
                    _refresh_candidate(previous_selection, selected)
                    selected = previous_selection
                job.selected_candidate_id = selected.id if selected else None
                job.decision = {
                    "mode": "dry-run" if policy.dry_run else "live",
                    "candidate_count": len(candidates),
                    "selected_title": selected.title if selected else None,
                    "selected_score": selected.score if selected else None,
                    "rejected": rejected,
                }
                _record_search_outcome(media, found_candidate=selected is not None)
                if selected is None and media.next_search_at is not None:
                    job.decision = {
                        **job.decision,
                        "search_cooldown_until": media.next_search_at.isoformat(),
                    }
                await session.commit()

        if selected is not None and not policy.dry_run:
            if existing_download is not None and existing_download.state != DownloadState.ERROR:
                job.decision = {
                    **job.decision,
                    "download_state": existing_download.state.value,
                }
                if existing_download.state == DownloadState.OUTCOME_UNKNOWN:
                    await session.commit()
                    raise AppError(
                        "QB_ADD_OUTCOME_UNKNOWN",
                        "qBittorrent 写入结果未知，请等待状态对账",
                        status_code=504,
                    )
                if existing_download.state == DownloadState.SUBMITTING:
                    await session.commit()
                    raise AppError(
                        "DOWNLOAD_SUBMISSION_INCOMPLETE",
                        "已有下载仍处于提交中状态，请先同步或处理原任务",
                        status_code=409,
                    )
                job.state = AutomationJobState.SUCCEEDED
                job.finished_at = utc_now()
                await session.commit()
                return True
            budget_reason = await _budget_reason(
                session,
                policy,
                selected,
                exclude_download_id=(
                    existing_download.id if existing_download is not None else None
                ),
            )
            if budget_reason is not None:
                job.decision = {**job.decision, "download_skipped": budget_reason}
                tomorrow = utc_now().astimezone(_SHANGHAI).date() + timedelta(days=1)
                job.state = AutomationJobState.RETRY_WAIT
                job.next_attempt_at = datetime.combine(
                    tomorrow, datetime.min.time(), _SHANGHAI
                ).astimezone(UTC)
                job.finished_at = utc_now()
                await session.commit()
                return None
            else:
                assert pt_factory is not None
                assert qb_factory is not None

                async def link_download(download: Download) -> None:
                    job.download_id = download.id
                    await session.commit()

                async def guard_write(download: Download) -> None:
                    current_policy = await session.get(
                        AutomationPolicy, policy_id, populate_existing=True
                    )
                    if current_policy is None:
                        raise AppError(
                            "AUTOMATION_POLICY_NOT_FOUND",
                            "自动化策略不存在，已停止下载提交",
                            status_code=409,
                        )
                    if not current_policy.enabled or current_policy.dry_run:
                        raise AppError(
                            "AUTOMATION_POLICY_CHANGED",
                            "自动化已暂停或切换为演练模式，已停止下载提交",
                            status_code=409,
                        )
                    current_media = await session.get(
                        LibraryMediaItem, job.media_id, populate_existing=True
                    )
                    current_candidate = await session.get(
                        ReleaseCandidate, selected.id, populate_existing=True
                    )
                    if current_media is None or current_candidate is None:
                        raise AppError(
                            "AUTOMATION_TARGET_NOT_FOUND",
                            "自动化目标或候选已不存在，已停止下载提交",
                            status_code=409,
                        )
                    accepted, rejected = _choose_candidate(
                        current_policy,
                        [current_candidate],
                        media_type=current_media.media_type,
                        require_known_size=current_policy.daily_download_bytes is not None,
                    )
                    if current_media.media_type.value not in current_policy.media_types:
                        accepted = None
                        rejected.append(
                            {
                                "candidate_id": current_candidate.id,
                                "title": current_candidate.title,
                                "reasons": ["媒体类型已从当前策略中移除"],
                            }
                        )
                    if not _media_in_policy_scope(current_policy, current_media):
                        accepted = None
                        rejected.append(
                            {
                                "candidate_id": current_candidate.id,
                                "title": current_candidate.title,
                                "reasons": ["影视已从当前自动化范围中移除"],
                            }
                        )
                    if accepted is None:
                        job.decision = {
                            **job.decision,
                            "download_skipped": "候选不再符合当前自动化策略",
                            "rejected": rejected,
                        }
                        raise AppError(
                            "AUTOMATION_CANDIDATE_REJECTED",
                            "候选不再符合当前自动化策略，已停止下载提交",
                            status_code=409,
                        )
                    actual_size = download.content_size_bytes
                    if actual_size is None:
                        raise AppError(
                            "AUTOMATION_CONTENT_SIZE_UNKNOWN",
                            "无法确认种子实际体积，已停止下载提交",
                            status_code=409,
                        )
                    if (
                        current_policy.max_size_bytes is not None
                        and actual_size > current_policy.max_size_bytes
                    ):
                        raise AppError(
                            "AUTOMATION_CONTENT_SIZE_EXCEEDED",
                            "种子实际体积超过单资源上限，已停止下载提交",
                            status_code=409,
                        )
                    budget_reason = await _budget_reason(
                        session,
                        current_policy,
                        current_candidate,
                        candidate_size_bytes=actual_size,
                        exclude_download_id=download.id,
                    )
                    if budget_reason is not None:
                        raise AppError(
                            "AUTOMATION_BUDGET_EXCEEDED",
                            f"{budget_reason}，已停止下载提交",
                            status_code=409,
                        )

                # The selector and the write guard already enforce hard warnings and
                # the policy's allow_warnings setting for automated submissions.
                download = await submit_download(
                    session,
                    candidate_id=selected.id,
                    confirm_warnings=True,
                    pt_factory=pt_factory,
                    qb_factory=qb_factory,
                    on_download=link_download,
                    write_guard=guard_write,
                )
                job.download_id = download.id
                job.decision = {**job.decision, "download_state": download.state.value}
                await session.commit()
                if download.state == DownloadState.OUTCOME_UNKNOWN:
                    raise AppError(
                        "QB_ADD_OUTCOME_UNKNOWN",
                        "qBittorrent 写入结果未知，请等待状态对账",
                        status_code=504,
                    )
                if download.state in {DownloadState.ERROR, DownloadState.SUBMITTING}:
                    raise AppError(
                        "DOWNLOAD_NOT_SUBMITTED",
                        "下载记录未成功提交，请先处理原任务",
                        status_code=409,
                    )

        job.state = AutomationJobState.SUCCEEDED
        job.finished_at = utc_now()
        await session.commit()
        return True
    except Exception as exc:
        await session.rollback()
        stored_job = await session.get(AutomationJob, job_id)
        if stored_job is None:
            raise
        job = stored_job
        stored_policy = await session.get(AutomationPolicy, policy_id)
        if stored_policy is None:
            raise
        policy = stored_policy
        job.error_message = exc.message if isinstance(exc, AppError) else "自动搜索或下载失败"
        job.finished_at = utc_now()
        will_retry = (
            isinstance(exc, AppError) and exc.retryable and job.attempt_count < policy.max_attempts
        )
        if will_retry:
            delay = min(
                policy.retry_delay_minutes * (2 ** (job.attempt_count - 1)),
                7 * 24 * 60,
            )
            job.state = AutomationJobState.RETRY_WAIT
            job.next_attempt_at = utc_now() + timedelta(minutes=delay)
        else:
            job.state = AutomationJobState.FAILED
            job.next_attempt_at = None
            if not isinstance(exc, AppError):
                _logger.exception("Unexpected automation job failure", exc_info=exc)
        await session.commit()
        return None if will_retry else False


def _copy_job_for_run(
    source: AutomationJob,
    *,
    run_id: str,
    trigger: str,
    reset_attempts: bool,
) -> AutomationJob:
    return AutomationJob(
        run_id=run_id,
        media_id=source.media_id,
        state=AutomationJobState.PENDING,
        search_id=source.search_id,
        selected_candidate_id=source.selected_candidate_id,
        download_id=source.download_id,
        retry_of_job_id=source.id,
        trigger=trigger,
        attempt_count=0 if reset_attempts else source.attempt_count,
        decision=dict(source.decision or {}),
    )


def _supersede_job(source: AutomationJob) -> None:
    """Close the queue entry without rewriting the original run's outcome."""

    source.superseded_at = utc_now()


def _media_in_policy_scope(policy: AutomationPolicy, media: LibraryMediaItem) -> bool:
    if policy.scope_mode == "selected":
        return media.id in set(policy.selected_media_ids)
    if not policy.regions:
        return True
    allowed_regions = {NextFindRegion(value) for value in policy.regions}
    return bool(
        allowed_regions.intersection(nextfind_regions(media.country_codes, media.original_language))
    )


async def _budget_reason(
    session: AsyncSession,
    policy: AutomationPolicy,
    candidate: ReleaseCandidate,
    *,
    candidate_size_bytes: int | None = None,
    exclude_download_id: str | None = None,
) -> str | None:
    now_shanghai = utc_now().astimezone(_SHANGHAI)
    day_start = datetime.combine(now_shanghai.date(), datetime.min.time(), _SHANGHAI).astimezone(
        UTC
    )
    automated_download_ids = select(AutomationJob.download_id).where(
        AutomationJob.download_id.is_not(None)
    )
    filters = [
        Download.submitted_at >= day_start,
        Download.id.in_(automated_download_ids),
    ]
    if exclude_download_id is not None:
        filters.append(Download.id != exclude_download_id)
    count = await session.scalar(select(func.count()).select_from(Download).where(*filters))
    if int(count or 0) >= policy.daily_download_limit:
        return "已达到每日自动下载数量上限"
    if policy.daily_download_bytes is not None:
        used = await session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            Download.content_size_bytes,
                            ReleaseCandidate.size_bytes,
                            0,
                        )
                    ),
                    0,
                )
            )
            .select_from(Download)
            .join(ReleaseCandidate, ReleaseCandidate.id == Download.candidate_id)
            .where(*filters)
        )
        candidate_size = (
            candidate_size_bytes if candidate_size_bytes is not None else candidate.size_bytes or 0
        )
        if int(used or 0) + candidate_size > policy.daily_download_bytes:
            return "将超过每日自动下载体积上限"
    return None


def _choose_candidate(
    policy: AutomationPolicy,
    candidates: list[ReleaseCandidate],
    *,
    media_type: MediaType,
    require_known_size: bool = False,
) -> tuple[ReleaseCandidate | None, list[dict[str, object]]]:
    rejected: list[dict[str, object]] = []
    eligible: list[ReleaseCandidate] = []
    for candidate in candidates:
        reasons: list[str] = []
        warnings = candidate.warnings or []
        warning_set = set(warnings)
        match_reasons = candidate.reasons or []
        match_reason_set = set(match_reasons)
        exact_identity = bool(_EXACT_IDENTITY_REASONS.intersection(match_reason_set))
        if policy.site_ids and candidate.site_id not in policy.site_ids:
            reasons.append("候选站点已从当前策略中移除")
        hard_warnings = _HARD_CORRECTNESS_WARNINGS.intersection(warning_set)
        if "ID_MISMATCH" in hard_warnings:
            reasons.append("站点提供的 TMDB 或 IMDb 与目标影视不匹配")
        if "ID_UNVERIFIED" in hard_warnings and not exact_identity:
            reasons.append("无法验证站点提供的 TMDB 或 IMDb")
        if "YEAR_MISMATCH" in hard_warnings and not exact_identity:
            reasons.append("资源年份与目标影视不匹配")
        if "TV_PACK_UNVERIFIED" in hard_warnings:
            reasons.append("无法确认资源为全集包或完整季包")
        if "PARTIAL_PACK" in warning_set and media_type != MediaType.TV:
            reasons.append("资源不是完整资源包")
        if not exact_identity and not {"ID_MISMATCH", "ID_UNVERIFIED"}.intersection(warning_set):
            missing_fallback = _FALLBACK_IDENTITY_REASONS - match_reason_set
            if "TITLE_EXACT" in missing_fallback:
                reasons.append("缺少精确 ID，且资源标题未与影视名称或别名精确匹配")
            if "YEAR_MATCH" in missing_fallback:
                reasons.append("缺少精确 ID，且无法确认资源年份匹配")
            if "MEDIA_TYPE_MATCH" in missing_fallback:
                reasons.append("缺少精确 ID，且无法确认影视类型匹配")
        if (
            media_type == MediaType.TV
            and not _tv_pack_rank(candidate)
            and "无法确认资源为全集包或完整季包" not in reasons
        ):
            reasons.append("无法确认资源为全集包或完整季包")
        if candidate.score < policy.minimum_score:
            reasons.append("评分低于策略门槛")
        if (candidate.seeders or 0) <= 0:
            reasons.append("当前没有做种，不能自动下载")
        elif (candidate.seeders or 0) < policy.minimum_seeders:
            reasons.append("做种数不足")
        if candidate.size_bytes is None and (policy.max_size_bytes or require_known_size):
            reasons.append("资源体积未知")
        if (
            policy.max_size_bytes is not None
            and candidate.size_bytes is not None
            and candidate.size_bytes > policy.max_size_bytes
        ):
            reasons.append("资源体积超过策略上限")
        soft_warnings = warning_set - _HARD_CORRECTNESS_WARNINGS
        if media_type == MediaType.TV:
            # Older stored candidates may still carry missing-episode warnings.
            # They are irrelevant when the operator replaces the local TV item
            # with a complete series or season pack.
            soft_warnings.discard("PARTIAL_PACK")
            soft_warnings.discard("EPISODE_OVERLAP")
        if soft_warnings and not policy.allow_warnings:
            reasons.append("候选包含风险提示")
        if reasons:
            rejected.append(
                {"candidate_id": candidate.id, "title": candidate.title, "reasons": reasons}
            )
        else:
            eligible.append(candidate)

    if not eligible:
        return None, rejected

    ranked = sorted(
        eligible,
        key=lambda candidate: _candidate_rank(candidate, media_type=media_type),
        reverse=True,
    )
    selected = ranked[0]
    rejected.extend(
        {
            "candidate_id": candidate.id,
            "title": candidate.title,
            "reasons": ["符合硬性条件，但综合排序低于已选资源"],
        }
        for candidate in ranked[1:]
    )
    return selected, rejected


def _same_torrent(left: ReleaseCandidate, right: ReleaseCandidate) -> bool:
    return left.site_id == right.site_id and left.torrent_id == right.torrent_id


def _refresh_candidate(target: ReleaseCandidate, source: ReleaseCandidate) -> None:
    for field in (
        "title",
        "size_bytes",
        "seeders",
        "resolution",
        "source",
        "codec",
        "download_factor",
        "collection_type",
        "file_count",
        "season_coverage",
        "episode_coverage",
        "score",
        "reasons",
        "warnings",
        "info_hash",
    ):
        setattr(target, field, getattr(source, field))


def _candidate_rank(
    candidate: ReleaseCandidate, *, media_type: MediaType
) -> tuple[int | float, ...]:
    reasons = set(candidate.reasons or [])
    identity_rank = 2 if _EXACT_IDENTITY_REASONS.intersection(reasons) else 1
    coverage_rank = _tv_pack_rank(candidate) if media_type == MediaType.TV else 0
    seeders = candidate.seeders or 0
    seeder_health = 2 if seeders >= 3 else 1
    download_factor = candidate.download_factor
    promotion_rank = (
        2 if download_factor == 0 else int(download_factor is not None and download_factor < 1)
    )
    return (
        identity_rank,
        coverage_rank,
        _resolution_rank(candidate.resolution),
        _source_rank(candidate.source),
        seeder_health,
        promotion_rank,
        candidate.size_bytes or 0,
        seeders,
        candidate.score,
    )


def _tv_pack_rank(candidate: ReleaseCandidate) -> int:
    return tv_pack_rank(
        classify_tv_pack(
            candidate.title,
            collection_type=candidate.collection_type,
            seasons=candidate.season_coverage,
            episodes=candidate.episode_coverage,
            file_count=candidate.file_count,
        )
    )


def _resolution_rank(value: str | None) -> int:
    if not value:
        return 0
    match = re.search(r"(2160|1080|720|576|480|360)", value)
    return int(match.group(1)) if match else 0


def _source_rank(value: str | None) -> int:
    normalized = re.sub(r"[^a-z0-9]", "", (value or "").casefold())
    if "remux" in normalized:
        return 5
    if "bluray" in normalized or "bdrip" in normalized:
        return 4
    if "webdl" in normalized:
        return 3
    if "webrip" in normalized:
        return 2
    if "hdtv" in normalized:
        return 1
    return 0
