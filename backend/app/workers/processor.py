from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter, MetadataProvider, PtSiteAdapter
from app.adapters.pt_sites.registry import PtSiteRegistry, default_pt_site_registry
from app.core.config import get_settings
from app.core.episodes import derive_missing_episode_codes
from app.core.security import sanitize_details
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    AuditEvent,
    DiscoveryRun,
    IdentityReview,
    Job,
    MediaItem,
    MetadataMatch,
    TorrentCandidateRecord,
    TorrentSearchRun,
    WorkerHeartbeat,
)
from app.models.enums import JobStatus, MediaType, MetadataStatus, WorkflowStatus
from app.schemas.adapters import (
    DiscoveryWarning,
    MediaItemData,
    MetadataRecord,
    PtSearchMode,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.schemas.entities import TorrentSearchCreateRequest
from app.services.automation import (
    maybe_automate_identity,
    maybe_automate_torrent_search_after_identity,
    maybe_automate_torrent_selection,
    require_automatic_torrent_search_current,
)
from app.services.matching import MatchPreferences, score_metadata_match, score_torrent_candidate
from app.services.workflow import (
    auto_confirm_strict_identity,
    cancel_active_metadata_resolution_jobs,
    cancel_metadata_resolution_job,
    enqueue_metadata_resolution,
    identity_finalization_evidence,
    metadata_resolution_input_fingerprint,
    refresh_media_search_workflow_status,
    torrent_search_input_fingerprint,
)


@dataclass(frozen=True, slots=True)
class MetadataResolutionInput:
    media_id: str
    media_type: MediaType
    tmdb_id: int | None
    title: str
    year: int | None


class JobProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        adapter_factory: Callable[[], MediaSourceAdapter],
        metadata_provider_factory: Callable[[], MetadataProvider] | None = None,
        pt_site_factory: Callable[[], PtSiteAdapter] | None = None,
        *,
        pt_site_registry: PtSiteRegistry | None = None,
        lease_seconds: int = 300,
        lease_renew_interval_seconds: float = 60,
        heartbeat_interval_seconds: float = 10,
        country_checkpoint_batch_size: int = 100,
        auto_enqueue_metadata_resolution: bool = False,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not 0 < lease_renew_interval_seconds < lease_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        if country_checkpoint_batch_size <= 0:
            raise ValueError("country checkpoint batch size must be positive")
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.adapter_factory = adapter_factory
        self.metadata_provider_factory = metadata_provider_factory
        if pt_site_factory is not None and pt_site_registry is not None:
            raise ValueError("provide either pt_site_factory or pt_site_registry, not both")
        self.pt_site_registry = pt_site_registry or PtSiteRegistry()
        if pt_site_factory is not None:
            self.pt_site_registry = default_pt_site_registry(pt_site_factory)
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = lease_renew_interval_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.country_checkpoint_batch_size = country_checkpoint_batch_size
        self.auto_enqueue_metadata_resolution = auto_enqueue_metadata_resolution

    async def heartbeat(self) -> None:
        async with self.session_factory() as session:
            heartbeat = await session.get(WorkerHeartbeat, self.worker_id)
            if heartbeat is None:
                session.add(WorkerHeartbeat(worker_id=self.worker_id, last_seen_at=utc_now()))
            else:
                heartbeat.last_seen_at = utc_now()
            await session.commit()

    async def claim_job(self) -> Job | None:
        now = utc_now()
        stale_before = now - timedelta(seconds=self.lease_seconds)
        claimable = or_(
            Job.status == JobStatus.PENDING,
            and_(
                Job.status == JobStatus.RETRY_WAIT,
                or_(Job.next_retry_at.is_(None), Job.next_retry_at <= now),
            ),
            and_(
                Job.status == JobStatus.RUNNING,
                Job.locked_at.is_not(None),
                Job.locked_at < stale_before,
            ),
        )
        async with self.session_factory() as session:
            statement = (
                select(Job)
                .where(
                    claimable,
                    Job.attempts < Job.max_attempts,
                    Job.job_type.not_like("AUTOMATION_PREFLIGHT:%"),
                )
                .order_by(Job.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = await session.scalar(statement)
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.locked_at = now
            job.locked_by = self.worker_id
            job.lease_token = str(uuid.uuid4())
            job.next_retry_at = None
            run = await session.get(DiscoveryRun, job.run_id) if job.run_id else None
            if run is not None:
                run.status = JobStatus.RUNNING
                run.started_at = run.started_at or now
                run.error_code = None
                run.error_message = None
                session.add(
                    AuditEvent(
                        event_type="DISCOVERY_RUN_STARTED",
                        entity_type="discovery_run",
                        entity_id=run.id,
                        sanitized_details={"attempt": job.attempts, "worker": self.worker_id},
                    )
                )
            media_id = job.payload.get("media_id")
            media: MediaItem | None = None
            if isinstance(media_id, str) and job.job_type.startswith("TORRENT_SEARCH:"):
                media = await session.get(
                    MediaItem,
                    media_id,
                    with_for_update=True,
                )
            search_run_id = job.payload.get("search_run_id")
            if isinstance(search_run_id, str):
                search_run = await session.get(TorrentSearchRun, search_run_id)
                if search_run is not None:
                    search_run.status = WorkflowStatus.PT_SEARCHING
                    search_run.started_at = search_run.started_at or now
                    if media is not None and job.job_type.startswith("TORRENT_SEARCH:"):
                        await refresh_media_search_workflow_status(session, media)
            await session.commit()
            return job

    async def run_once(self) -> bool:
        await self.heartbeat()
        job = await self.claim_job()
        if job is None:
            return False
        stop_renewal = asyncio.Event()
        work = asyncio.create_task(self._run_claimed_job(job))
        renewal = asyncio.create_task(self._renew_lease_loop(job.id, job.lease_token, stop_renewal))
        try:
            done, _ = await asyncio.wait({work, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return await work

            lease_maintained = await renewal
            if not lease_maintained:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                return True
            return await work
        finally:
            stop_renewal.set()
            for task in (work, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, renewal, return_exceptions=True)

    async def _run_claimed_job(self, job: Job) -> bool:
        if job.job_type == "DISCOVER_NEXTFIND" and job.run_id:
            await self._run_discovery(job)
            return True
        if job.job_type.startswith("RESOLVE_METADATA:"):
            await self._run_metadata_resolution(job)
            return True
        if job.job_type.startswith("TORRENT_SEARCH:"):
            await self._run_torrent_search(job)
            return True
        await self._fail(
            job.id,
            AppError("UNKNOWN_JOB_TYPE", "无法识别的任务类型"),
            lease_token=job.lease_token,
        )
        return True

    async def _renew_lease_loop(
        self, job_id: str, lease_token: str | None, stop: asyncio.Event
    ) -> bool:
        if lease_token is None:
            return False
        loop = asyncio.get_running_loop()
        next_renewal = loop.time() + self.lease_renew_interval_seconds
        while True:
            timeout = min(
                self.heartbeat_interval_seconds,
                max(0.0, next_renewal - loop.time()),
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout)
                return True
            except TimeoutError:
                await self.heartbeat()
                if loop.time() < next_renewal:
                    continue
                if not await self.renew_lease(job_id, lease_token):
                    return False
                next_renewal = loop.time() + self.lease_renew_interval_seconds

    async def renew_lease(self, job_id: str, lease_token: str) -> bool:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token):
                return False
            job.locked_at = utc_now()
            await session.commit()
            return True

    def _owns_lease(self, job: Job | None, lease_token: str | None) -> bool:
        return bool(
            job is not None
            and lease_token
            and job.status == JobStatus.RUNNING
            and job.locked_by == self.worker_id
            and job.lease_token == lease_token
        )

    async def _run_discovery(self, job: Job) -> None:
        adapter: MediaSourceAdapter | None = None
        try:
            adapter = self.adapter_factory()
            await adapter.authenticate()
            result = await adapter.list_missing_media()
            items, warnings = await self._enrich_library_details(
                adapter, result.items, result.warnings
            )
            items = await self._reuse_persisted_country_codes(items)

            async def checkpoint_country_codes(
                values: dict[tuple[MediaType, int], list[str]],
            ) -> None:
                await self._checkpoint_country_codes(
                    job.id,
                    job.lease_token,
                    values,
                )

            items, warnings = await self._enrich_country_codes(
                items,
                warnings,
                checkpoint=checkpoint_country_codes,
            )
            if job.run_id is not None:
                await self._complete_discovery(
                    job.id,
                    job.run_id,
                    items,
                    warnings,
                    lease_token=job.lease_token,
                )
        except AppError as exc:
            await self._fail(job.id, exc, lease_token=job.lease_token)
        except Exception as exc:  # defensive boundary: never expose exception text
            await self._fail(
                job.id,
                AppError(
                    "INTERNAL_WORKER_ERROR",
                    "Worker 处理任务时发生内部错误",
                    retryable=False,
                ),
                lease_token=job.lease_token,
            )
            del exc
        finally:
            close = getattr(adapter, "aclose", None) if adapter is not None else None
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    async def _run_metadata_resolution(self, job: Job) -> None:
        provider: MetadataProvider | None = None
        try:
            media_id = job.payload.get("media_id")
            if not isinstance(media_id, str):
                raise AppError("INVALID_JOB_PAYLOAD", "元数据任务参数无效")
            media = await self._prepare_metadata_resolution(
                job.id,
                media_id,
                lease_token=job.lease_token,
            )
            if media is None:
                return
            if self.metadata_provider_factory is None:
                raise AppError("TMDB_LIVE_DISABLED", "TMDB 真实只读 Provider 未启用")
            provider = self.metadata_provider_factory()
            candidates: list[MetadataRecord]
            if media.tmdb_id is not None:
                try:
                    candidates = [await provider.get_by_tmdb_id(media.media_type, media.tmdb_id)]
                except AppError as exc:
                    if exc.error_code != "TMDB_NOT_FOUND":
                        raise
                    opposite = (
                        MediaType.TV if media.media_type == MediaType.MOVIE else MediaType.MOVIE
                    )
                    try:
                        candidates = [await provider.get_by_tmdb_id(opposite, media.tmdb_id)]
                    except AppError as opposite_error:
                        if opposite_error.error_code == "TMDB_NOT_FOUND":
                            raise exc from opposite_error
                        raise
            else:
                candidates = await provider.search(media.media_type, media.title, media.year)
            await self._complete_metadata_resolution(
                job.id, media.media_id, candidates, lease_token=job.lease_token
            )
        except AppError as exc:
            await self._fail(job.id, exc, lease_token=job.lease_token)
        except Exception as exc:
            await self._fail(
                job.id,
                AppError("INTERNAL_WORKER_ERROR", "Worker 解析影视身份时发生内部错误"),
                lease_token=job.lease_token,
            )
            del exc
        finally:
            await self._close_adapter(provider)

    async def _prepare_metadata_resolution(
        self,
        job_id: str,
        media_id: str,
        *,
        lease_token: str | None,
    ) -> MetadataResolutionInput | None:
        async with self.session_factory() as session:
            media = await session.get(MediaItem, media_id, with_for_update=True)
            if media is None:
                raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
            job = await session.scalar(
                select(Job).where(Job.id == job_id).with_for_update().limit(1)
            )
            if job is None or not self._owns_lease(job, lease_token):
                return None
            evidence = await identity_finalization_evidence(session, media)
            if evidence is not None:
                cancel_metadata_resolution_job(
                    session,
                    job,
                    media_id=media.id,
                    evidence=evidence,
                )
                await session.commit()
                return None
            return MetadataResolutionInput(
                media_id=media.id,
                media_type=media.media_type,
                tmdb_id=media.tmdb_id,
                title=media.title,
                year=media.year,
            )

    async def _complete_metadata_resolution(
        self,
        job_id: str,
        media_id: str,
        candidates: list[MetadataRecord],
        *,
        lease_token: str | None,
    ) -> None:
        async with self.session_factory() as session:
            media = await session.get(MediaItem, media_id, with_for_update=True)
            if media is None:
                return
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token):
                return
            evidence = await identity_finalization_evidence(session, media)
            if evidence is not None:
                cancel_metadata_resolution_job(
                    session,
                    job,
                    media_id=media.id,
                    evidence=evidence,
                )
                await session.commit()
                return
            expected_fingerprint = job.payload.get("input_fingerprint")
            if isinstance(
                expected_fingerprint, str
            ) and expected_fingerprint != metadata_resolution_input_fingerprint(media):
                raise AppError(
                    "METADATA_INPUT_CHANGED",
                    "影视身份输入在 TMDB 查询期间发生变化，旧结果已丢弃",
                    retryable=False,
                )
            unique: dict[int, MetadataRecord] = {
                candidate.tmdb_id: candidate for candidate in candidates
            }
            ranked = sorted(
                (
                    (
                        candidate,
                        score_metadata_match(media, candidate, expected_tmdb_id=media.tmdb_id),
                    )
                    for candidate in unique.values()
                ),
                key=lambda item: item[1].score,
                reverse=True,
            )[:5]
            exact_tv_candidate = next(
                (
                    candidate
                    for candidate, match in ranked
                    if media.tmdb_id is not None
                    and candidate.tmdb_id == media.tmdb_id
                    and media.media_type == MediaType.TV
                    and candidate.media_type == MediaType.TV
                    and not match.conflicts
                ),
                None,
            )
            if exact_tv_candidate is not None:
                media.missing_episodes = derive_missing_episode_codes(
                    exact_tv_candidate.episode_matrix,
                    media.local_episode_matrix,
                    media.missing_episodes,
                )
            stored_matches: list[MetadataMatch] = []
            for rank, (candidate, match) in enumerate(ranked, start=1):
                stored_match = MetadataMatch(
                    media_id=media.id,
                    resolution_job_id=job.id,
                    tmdb_id=candidate.tmdb_id,
                    rank=rank,
                    score=match.score,
                    match_reasons=match.reasons,
                    conflicts=match.conflicts,
                    candidate_snapshot=candidate.model_dump(mode="json"),
                )
                stored_matches.append(stored_match)
                session.add(stored_match)
            job.status = JobStatus.SUCCEEDED
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.error_code = None
            job.error_message = None
            media.workflow_status = WorkflowStatus.IDENTITY_REVIEW
            media.metadata_status = MetadataStatus.NEEDS_CONFIRMATION
            await session.flush()
            strict_review = await auto_confirm_strict_identity(
                session,
                media,
                stored_matches,
                resolution_job_id=job.id,
            )
            review = strict_review
            if review is None:
                review = await maybe_automate_identity(
                    session,
                    job=job,
                    media=media,
                    settings=get_settings(),
                )
            else:
                await maybe_automate_torrent_search_after_identity(
                    session,
                    media=media,
                    trigger_created_at=review.created_at,
                    settings=get_settings(),
                )
            if review is not None and isinstance(job.payload.get("download_batch_id"), str):
                search_job = await session.scalar(
                    select(Job)
                    .where(
                        Job.job_type.like(f"TORRENT_SEARCH:%:{media.id}"),
                        Job.status.in_(
                            (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT)
                        ),
                    )
                    .order_by(Job.created_at.desc())
                    .limit(1)
                )
                if search_job is not None:
                    search_job.payload = {
                        **search_job.payload,
                        "download_batch_id": job.payload["download_batch_id"],
                        "download_batch_mode": job.payload.get("download_batch_mode"),
                        "download_batch_launch_mode": job.payload.get(
                            "download_batch_launch_mode"
                        ),
                        "download_batch_site_id": job.payload.get("download_batch_site_id"),
                        "download_batch_preferences": job.payload.get(
                            "download_batch_preferences"
                        ),
                    }
            session.add(
                AuditEvent(
                    event_type="METADATA_CANDIDATES_READY",
                    entity_type="media_item",
                    entity_id=media.id,
                    sanitized_details={
                        "candidate_count": len(ranked),
                        "used_exact_tmdb_id": media.tmdb_id is not None,
                        "manual_confirmation_required": review is None,
                        "strict_auto_confirmed": strict_review is not None,
                    },
                )
            )
            await session.commit()

    async def _run_torrent_search(self, job: Job) -> None:
        adapter: PtSiteAdapter | None = None
        try:
            media_id = job.payload.get("media_id")
            search_run_id = job.payload.get("search_run_id")
            payload_site_id = job.payload.get("site_id")
            if (
                not isinstance(media_id, str)
                or not isinstance(search_run_id, str)
                or (payload_site_id is not None and not isinstance(payload_site_id, str))
            ):
                raise AppError("INVALID_JOB_PAYLOAD", "PT 搜索任务参数无效")
            async with self.session_factory() as session:
                media = await session.get(MediaItem, media_id)
                search_run = await session.get(TorrentSearchRun, search_run_id)
                review = await session.scalar(
                    select(IdentityReview)
                    .where(
                        IdentityReview.media_id == media_id,
                        IdentityReview.status == "CONFIRMED",
                    )
                    .order_by(IdentityReview.created_at.desc())
                    .limit(1)
                )
                if media is None or search_run is None:
                    raise AppError("SEARCH_RUN_NOT_FOUND", "PT 搜索任务不存在", status_code=404)
                if search_run.media_id != media_id:
                    raise AppError(
                        "PT_MEDIA_BINDING_MISMATCH",
                        "PT search job, run, and media bindings do not match",
                        status_code=409,
                    )
                site_id = payload_site_id
                legacy_avistaz_job = False
                legacy_job_type = f"TORRENT_SEARCH:{media_id}"
                if site_id is None:
                    if job.job_type != legacy_job_type or search_run.site_id != "avistaz":
                        raise AppError(
                            "PT_SITE_BINDING_MISMATCH",
                            "旧版 PT 搜索任务无法安全推导站点绑定",
                            status_code=409,
                        )
                    site_id = "avistaz"
                    legacy_avistaz_job = True
                expected_job_types = {f"TORRENT_SEARCH:{site_id}:{media_id}"}
                if site_id == "avistaz":
                    expected_job_types.add(legacy_job_type)
                if search_run.site_id != site_id or job.job_type not in expected_job_types:
                    raise AppError(
                        "PT_SITE_BINDING_MISMATCH",
                        "PT 搜索任务、运行记录与站点绑定不一致",
                        status_code=409,
                    )
                if review is None:
                    raise AppError(
                        "IDENTITY_CONFIRMATION_REQUIRED", "影视身份尚未确认", status_code=409
                    )
                metadata = MetadataRecord.model_validate(review.candidate_snapshot)
                requested = self._torrent_search_request_from_snapshot(
                    search_run.sanitized_request,
                    allow_legacy_avistaz=legacy_avistaz_job,
                )
                if requested.site_id != site_id:
                    raise AppError(
                        "PT_SITE_BINDING_MISMATCH",
                        "PT 搜索请求快照与运行站点绑定不一致",
                        status_code=409,
                    )
                prior_titles = await self._prior_candidate_titles(session, media_id, site_id)
                declaration = self.pt_site_registry.catalog.require_searchable(
                    site_id,
                    media_type=media.media_type,
                )
            adapter = await self.pt_site_registry.create(site_id)
            before_request: Callable[[], Awaitable[None]] | None = None
            if isinstance(job.payload.get("automation_policy_revision_id"), str):

                async def guard_automatic_search() -> None:
                    await self._guard_automatic_torrent_search(job.id, job.lease_token)

                before_request = guard_automatic_search
                set_request_guard = getattr(adapter, "set_before_request_guard", None)
                if not callable(set_request_guard):
                    raise AppError(
                        "AUTOMATION_REQUEST_GUARD_UNAVAILABLE",
                        "PT 适配器无法安装逐请求自动化围栏",
                        status_code=409,
                    )
                set_request_guard(before_request)
                await before_request()
            candidates, strategy_log = await self._search_with_fallbacks(
                adapter,
                metadata,
                requested,
                search_modes=declaration.search_modes,
                before_request=before_request,
            )
            if any(candidate.site_id != site_id for candidate in candidates):
                raise AppError(
                    "PT_SITE_CANDIDATE_MISMATCH",
                    "PT 适配器返回了其他站点的候选，结果已拒绝",
                    status_code=502,
                )
            settings = get_settings()
            preferences = MatchPreferences(
                resolutions=tuple(requested.preferred_resolutions)
                or settings.preferred_resolutions,
                sources=tuple(requested.preferred_sources) or settings.preferred_sources,
                audio=tuple(requested.preferred_audio) or settings.preferred_audio,
                subtitles=tuple(requested.preferred_subtitles) or settings.preferred_subtitles,
                max_size_bytes=requested.max_size_bytes or settings.max_candidate_size_bytes,
            )
            scored = []
            seen_titles = set(prior_titles)
            for candidate in candidates:
                normalized_title = candidate.release_title.casefold().strip()
                candidate_preferences = MatchPreferences(
                    resolutions=preferences.resolutions,
                    sources=preferences.sources,
                    audio=preferences.audio,
                    subtitles=preferences.subtitles,
                    max_size_bytes=preferences.max_size_bytes,
                    possible_duplicate=normalized_title in seen_titles,
                )
                scored.append(
                    score_torrent_candidate(
                        metadata,
                        candidate,
                        missing_episodes=media.missing_episodes,
                        preferences=candidate_preferences,
                    )
                )
                seen_titles.add(normalized_title)
            await self._complete_torrent_search(
                job.id,
                search_run_id,
                media_id,
                scored,
                strategy_log,
                lease_token=job.lease_token,
            )
        except AppError as exc:
            await self._fail(job.id, exc, lease_token=job.lease_token)
        except Exception as exc:
            await self._fail(
                job.id,
                AppError("INTERNAL_WORKER_ERROR", "Worker 搜索 PT 候选时发生内部错误"),
                lease_token=job.lease_token,
            )
            del exc
        finally:
            await self._close_adapter(adapter)

    async def _search_with_fallbacks(
        self,
        adapter: PtSiteAdapter,
        metadata: MetadataRecord,
        requested: TorrentSearchCreateRequest,
        *,
        search_modes: tuple[PtSearchMode, ...],
        before_request: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[list[TorrentCandidate], list[dict[str, object]]]:
        common: dict[str, Any] = {
            "type": metadata.media_type,
            "page": 1,
            "limit": 50,
            "video_quality": requested.preferred_resolutions,
            "subtitle": requested.preferred_subtitles,
        }
        strategies: list[tuple[str, TorrentSearchRequest]] = []
        if PtSearchMode.TMDB_ID in search_modes:
            strategies.append(("TMDB_ID", TorrentSearchRequest(tmdb=metadata.tmdb_id, **common)))
        if PtSearchMode.IMDB_ID in search_modes and metadata.imdb_id:
            strategies.append(("IMDB_ID", TorrentSearchRequest(imdb=metadata.imdb_id, **common)))
        if PtSearchMode.TEXT in search_modes:
            text_values = [
                ("ENGLISH_TITLE", metadata.english_title),
                ("ORIGINAL_TITLE", metadata.original_title),
                (
                    "CHINESE_OR_ALIAS",
                    metadata.chinese_title or next(iter(metadata.aliases), None),
                ),
                ("CANONICAL_TITLE", metadata.title),
            ]
            seen_queries: set[str] = set()
            for name, value in text_values:
                if not value:
                    continue
                queries = [(f"{name}_YEAR", f"{value} {metadata.year}")] if metadata.year else []
                queries.append((name, value))
                for strategy_name, query in queries:
                    if query.casefold() in seen_queries:
                        continue
                    seen_queries.add(query.casefold())
                    strategies.append((strategy_name, TorrentSearchRequest(search=query, **common)))
        log: list[dict[str, object]] = []
        for name, request in strategies:
            if before_request is not None:
                await before_request()
            results = await adapter.search(request)
            log.append({"strategy": name, "candidate_count": len(results)})
            if results:
                return list(results), log
        return [], log

    @staticmethod
    def _torrent_search_request_from_snapshot(
        snapshot: dict[str, object],
        *,
        allow_legacy_avistaz: bool,
    ) -> TorrentSearchCreateRequest:
        payload = dict(snapshot)
        if "site_id" not in payload and allow_legacy_avistaz:
            payload["site_id"] = "avistaz"
        try:
            return TorrentSearchCreateRequest.model_validate(payload)
        except ValidationError as exc:
            raise AppError(
                "PT_SEARCH_REQUEST_SNAPSHOT_INVALID",
                "PT 搜索请求快照无效",
                status_code=409,
            ) from exc

    async def _guard_automatic_torrent_search(self, job_id: str, lease_token: str | None) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or not self._owns_lease(job, lease_token):
                raise AppError(
                    "AUTOMATION_TORRENT_SEARCH_LEASE_LOST",
                    "自动 PT 搜索任务租约已失效，禁止外部请求",
                    status_code=409,
                )
            await require_automatic_torrent_search_current(
                session,
                job=job,
                settings=get_settings(),
            )

    @staticmethod
    async def _prior_candidate_titles(
        session: AsyncSession, media_id: str, site_id: str
    ) -> set[str]:
        statement = (
            select(TorrentCandidateRecord.candidate_snapshot)
            .join(TorrentSearchRun, TorrentSearchRun.id == TorrentCandidateRecord.search_run_id)
            .where(
                TorrentSearchRun.media_id == media_id,
                TorrentSearchRun.site_id == site_id,
            )
        )
        snapshots = (await session.scalars(statement)).all()
        return {
            str(snapshot.get("release_title", "")).casefold().strip()
            for snapshot in snapshots
            if snapshot.get("release_title")
        }

    async def _complete_torrent_search(
        self,
        job_id: str,
        search_run_id: str,
        media_id: str,
        candidates: list[TorrentCandidate],
        strategy_log: list[dict[str, object]],
        *,
        lease_token: str | None,
    ) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            run = await session.get(TorrentSearchRun, search_run_id, with_for_update=True)
            media = await session.get(MediaItem, media_id, with_for_update=True)
            if (
                job is None
                or not self._owns_lease(job, lease_token)
                or run is None
                or media is None
            ):
                return
            if (
                run.media_id != media.id
                or job.payload.get("media_id") != media.id
                or job.payload.get("search_run_id") != run.id
            ):
                raise AppError(
                    "PT_MEDIA_BINDING_MISMATCH",
                    "PT search job, run, and media bindings do not match",
                    status_code=409,
                )
            review = await session.scalar(
                select(IdentityReview)
                .where(
                    IdentityReview.media_id == media.id,
                    IdentityReview.status == "CONFIRMED",
                )
                .order_by(IdentityReview.created_at.desc(), IdentityReview.id.desc())
                .limit(1)
            )
            if review is None:
                raise AppError(
                    "TORRENT_SEARCH_INPUT_STALE",
                    "PT 搜索完成时影视身份已变化",
                    status_code=409,
                )
            payload_site_id = job.payload.get("site_id")
            legacy_avistaz_job = (
                payload_site_id is None
                and job.job_type == f"TORRENT_SEARCH:{media.id}"
                and run.site_id == "avistaz"
            )
            requested = self._torrent_search_request_from_snapshot(
                run.sanitized_request,
                allow_legacy_avistaz=legacy_avistaz_job,
            )
            if requested.site_id != run.site_id or (
                isinstance(payload_site_id, str) and payload_site_id != run.site_id
            ):
                raise AppError(
                    "PT_SITE_BINDING_MISMATCH",
                    "PT 搜索完成时站点绑定不一致",
                    status_code=409,
                )
            expected_fingerprint = job.payload.get("input_fingerprint")
            current_fingerprint = torrent_search_input_fingerprint(
                media, review, requested, get_settings()
            )
            automatic_job = isinstance(job.payload.get("automation_policy_revision_id"), str)
            if (automatic_job and not isinstance(expected_fingerprint, str)) or (
                isinstance(expected_fingerprint, str)
                and expected_fingerprint != current_fingerprint
            ):
                raise AppError(
                    "TORRENT_SEARCH_INPUT_STALE",
                    "PT 搜索输入已变化，候选结果已丢弃",
                    status_code=409,
                )
            for candidate in candidates:
                session.add(
                    TorrentCandidateRecord(
                        search_run_id=run.id,
                        site_id=candidate.site_id,
                        torrent_id=candidate.torrent_id,
                        candidate_snapshot=candidate.model_dump(mode="json"),
                        match_score=candidate.match_score or 0,
                        match_reasons=candidate.match_reasons,
                        warnings=candidate.warnings,
                    )
                )
            now = utc_now()
            target_status = (
                WorkflowStatus.TORRENT_REVIEW if candidates else WorkflowStatus.NO_CANDIDATE
            )
            run.status = target_status
            run.strategy_log = strategy_log
            run.candidate_count = len(candidates)
            run.finished_at = now
            run.error_code = None
            run.error_message = None
            await refresh_media_search_workflow_status(session, media)
            job.status = JobStatus.SUCCEEDED
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.error_code = None
            job.error_message = None
            session.add(
                AuditEvent(
                    event_type=(
                        "TORRENT_CANDIDATES_READY" if candidates else "TORRENT_SEARCH_NO_CANDIDATE"
                    ),
                    entity_type="torrent_search_run",
                    entity_id=run.id,
                    sanitized_details={
                        "candidate_count": len(candidates),
                        "site_id": run.site_id,
                        "strategies": strategy_log,
                        "read_only": True,
                    },
                )
            )
            await session.flush()
            if candidates and job.payload.get("download_batch_mode") == "AUTO_SAFE":
                await maybe_automate_torrent_selection(
                    session,
                    job=job,
                    run=run,
                    media=media,
                    settings=get_settings(),
                )
            await session.commit()

    @staticmethod
    async def _close_adapter(adapter: object | None) -> None:
        close = getattr(adapter, "aclose", None) if adapter is not None else None
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    @staticmethod
    async def _enrich_library_details(
        adapter: MediaSourceAdapter,
        items: list[MediaItemData],
        warnings: list[DiscoveryWarning],
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning]]:
        enriched: list[MediaItemData] = []
        result_warnings = list(warnings)
        for item in items:
            needs_details = (
                item.media_type.value == "tv"
                and item.tmdb_id is not None
                and all(
                    value is None
                    for value in (
                        item.local_episodes,
                        item.total_episodes,
                        item.aired_episodes,
                    )
                )
            )
            if not needs_details or item.tmdb_id is None:
                enriched.append(item)
                continue
            try:
                details = await adapter.get_library_details(item.media_type, item.tmdb_id)
            except AppError as exc:
                if exc.retryable or exc.error_code in {
                    "AUTH_EXPIRED",
                    "AUTH_FORBIDDEN",
                    "AUTH_FAILED",
                }:
                    raise
                result_warnings.append(
                    DiscoveryWarning(
                        error_code="LIBRARY_DETAILS_ISOLATED",
                        message="单个媒体的本地详情无效，已保留未知字段",
                    )
                )
                enriched.append(item)
                continue
            except (ValidationError, ValueError):
                result_warnings.append(
                    DiscoveryWarning(
                        error_code="LIBRARY_DETAILS_ISOLATED",
                        message="单个媒体的本地详情无效，已保留未知字段",
                    )
                )
                enriched.append(item)
                continue
            updates = {
                field: value
                for field, value in {
                    "local_episodes": details.local_episodes,
                    "local_episode_matrix": details.local_episode_matrix,
                    "total_episodes": details.total_episodes,
                    "aired_episodes": details.aired_episodes,
                    "missing_episodes": details.missing_episodes,
                }.items()
                if value is not None
            }
            enriched.append(item.model_copy(update=updates))
        return enriched, result_warnings

    async def _enrich_country_codes(
        self,
        items: list[MediaItemData],
        warnings: list[DiscoveryWarning],
        *,
        checkpoint: Callable[[dict[tuple[MediaType, int], list[str]]], Awaitable[None]]
        | None = None,
    ) -> tuple[list[MediaItemData], list[DiscoveryWarning]]:
        pending_keys: list[tuple[MediaType, int]] = []
        seen_keys: set[tuple[MediaType, int]] = set()
        for item in items:
            if item.country_codes is not None or item.tmdb_id is None:
                continue
            key = (item.media_type, item.tmdb_id)
            if key not in seen_keys:
                seen_keys.add(key)
                pending_keys.append(key)
        if not pending_keys:
            return items, warnings

        result_warnings = list(warnings)
        if self.metadata_provider_factory is None:
            result_warnings.append(
                DiscoveryWarning(
                    error_code="COUNTRY_METADATA_UNAVAILABLE",
                    message="TMDB 未启用，部分影视的国家/地区暂时未知",
                )
            )
            return items, result_warnings

        provider: MetadataProvider | None = None
        try:
            try:
                provider = self.metadata_provider_factory()
            except AppError as exc:
                if exc.error_code not in {"TMDB_LIVE_DISABLED", "TMDB_NOT_CONFIGURED"}:
                    raise
                result_warnings.append(
                    DiscoveryWarning(
                        error_code="COUNTRY_METADATA_UNAVAILABLE",
                        message="TMDB 未启用，部分影视的国家/地区暂时未知",
                    )
                )
                return items, result_warnings

            country_codes_by_key: dict[tuple[MediaType, int], list[str] | None] = {}
            checkpoint_values: dict[tuple[MediaType, int], list[str]] = {}
            for index, (media_type, tmdb_id) in enumerate(pending_keys, start=1):
                try:
                    country_codes_by_key[(media_type, tmdb_id)] = await provider.get_country_codes(
                        media_type, tmdb_id
                    )
                except AppError as exc:
                    if exc.error_code != "TMDB_NOT_FOUND":
                        raise
                    result_warnings.append(
                        DiscoveryWarning(
                            error_code="COUNTRY_METADATA_ISOLATED",
                            message="单个影视的国家/地区无法确认，已保留为未知",
                        )
                    )
                    country_codes_by_key[(media_type, tmdb_id)] = None
                except (ValidationError, ValueError) as exc:
                    raise AppError(
                        "TMDB_VALIDATION_ERROR",
                        "TMDB 国家/地区字段校验失败",
                        status_code=502,
                    ) from exc
                country_codes = country_codes_by_key[(media_type, tmdb_id)]
                if country_codes is not None:
                    checkpoint_values[(media_type, tmdb_id)] = country_codes
                if (
                    checkpoint is not None
                    and index % self.country_checkpoint_batch_size == 0
                    and checkpoint_values
                ):
                    await checkpoint(checkpoint_values)
                    checkpoint_values = {}

            if checkpoint is not None and checkpoint_values:
                await checkpoint(checkpoint_values)

            enriched: list[MediaItemData] = []
            for item in items:
                if item.country_codes is not None or item.tmdb_id is None:
                    enriched.append(item)
                    continue
                country_codes = country_codes_by_key[(item.media_type, item.tmdb_id)]
                enriched.append(item.model_copy(update={"country_codes": country_codes}))
            return enriched, result_warnings
        finally:
            await self._close_adapter(provider)

    async def _checkpoint_country_codes(
        self,
        job_id: str,
        lease_token: str | None,
        values: dict[tuple[MediaType, int], list[str]],
    ) -> None:
        if not values:
            return
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token):
                raise AppError(
                    "JOB_LEASE_LOST",
                    "任务租约已失效",
                    status_code=409,
                    retryable=True,
                )
            for (media_type, tmdb_id), country_codes in values.items():
                await session.execute(
                    update(MediaItem)
                    .where(
                        MediaItem.media_type == media_type,
                        MediaItem.tmdb_id == tmdb_id,
                        MediaItem.country_codes.is_(None),
                    )
                    .values(country_codes=country_codes)
                )
            await session.commit()

    async def _reuse_persisted_country_codes(
        self, items: list[MediaItemData]
    ) -> list[MediaItemData]:
        unresolved = [item for item in items if item.country_codes is None]
        if not unresolved:
            return items

        tmdb_ids = {item.tmdb_id for item in unresolved if item.tmdb_id is not None}
        source_item_ids = {item.source_item_id for item in unresolved if item.tmdb_id is None}
        predicates = []
        if tmdb_ids:
            predicates.append(MediaItem.tmdb_id.in_(tmdb_ids))
        if source_item_ids:
            predicates.append(MediaItem.source_item_id.in_(source_item_ids))
        if not predicates:
            return items

        async with self.session_factory() as session:
            existing_items = (
                await session.scalars(
                    select(MediaItem).where(
                        MediaItem.country_codes.is_not(None),
                        or_(*predicates),
                    )
                )
            ).all()

        persisted: dict[tuple[str, str, int | str], list[str]] = {}
        for existing in existing_items:
            if existing.country_codes is None:
                continue
            identity: int | str = (
                existing.tmdb_id if existing.tmdb_id is not None else existing.source_item_id
            )
            persisted[(existing.source, existing.media_type.value, identity)] = (
                existing.country_codes
            )

        enriched: list[MediaItemData] = []
        for item in items:
            if item.country_codes is not None:
                enriched.append(item)
                continue
            identity = item.tmdb_id if item.tmdb_id is not None else item.source_item_id
            country_codes = persisted.get((item.source, item.media_type.value, identity))
            enriched.append(
                item
                if country_codes is None
                else item.model_copy(update={"country_codes": country_codes})
            )
        return enriched

    async def _complete_discovery(
        self,
        job_id: str,
        run_id: str,
        items: list[MediaItemData],
        warnings: list[DiscoveryWarning],
        *,
        lease_token: str | None,
    ) -> None:
        unique_items: dict[tuple[str, str, int | str], MediaItemData] = {}
        for item in items:
            identity: int | str = item.tmdb_id if item.tmdb_id is not None else item.source_item_id
            unique_items[(item.source, item.media_type.value, identity)] = item

        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            run = await session.get(DiscoveryRun, run_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token) or run is None:
                return
            created_count = 0
            updated_count = 0
            restored_count = 0
            seen_media: list[MediaItem] = []
            resolution_candidates: list[tuple[MediaItem, bool]] = []
            for item in unique_items.values():
                if item.tmdb_id is not None:
                    statement = select(MediaItem).where(
                        MediaItem.source == item.source,
                        MediaItem.media_type == item.media_type,
                        MediaItem.tmdb_id == item.tmdb_id,
                    )
                else:
                    statement = select(MediaItem).where(
                        MediaItem.source == item.source,
                        MediaItem.media_type == item.media_type,
                        MediaItem.tmdb_id.is_(None),
                        MediaItem.source_item_id == item.source_item_id,
                    )
                existing = await session.scalar(statement)
                values = item.model_dump(exclude={"discovered_at"})
                if existing is None:
                    media = MediaItem(**item.model_dump())
                    session.add(media)
                    resolution_candidates.append((media, False))
                    created_count += 1
                else:
                    was_in_library = existing.discovery_status.casefold() == "in_library"
                    existing.discovery_status = "MISSING"
                    previous_identity_fingerprint = metadata_resolution_input_fingerprint(existing)
                    for key, value in values.items():
                        if key == "country_codes" and value is None:
                            continue
                        setattr(existing, key, value)
                    if previous_identity_fingerprint != metadata_resolution_input_fingerprint(
                        existing
                    ):
                        resolution_candidates.append((existing, True))
                    if was_in_library and existing.discovery_status.casefold() == "missing":
                        restored_count += 1
                    media = existing
                    updated_count += 1
                seen_media.append(media)
            await session.flush()

            seen_media_ids = {media.id for media in seen_media}
            source_names = {item.source for item in unique_items.values()} or {run.source}
            stale_statement = select(MediaItem).where(
                MediaItem.source.in_(source_names),
                MediaItem.discovery_status == "MISSING",
            )
            if seen_media_ids:
                stale_statement = stale_statement.where(MediaItem.id.not_in(seen_media_ids))
            archived_items = list((await session.scalars(stale_statement)).all())
            reconciliation_time = utc_now()
            for archived in archived_items:
                archived.discovery_status = "IN_LIBRARY"
                archived.updated_at = reconciliation_time
                await cancel_active_metadata_resolution_jobs(
                    session,
                    archived.id,
                    evidence="NEXTFIND_ITEM_NO_LONGER_MISSING",
                )

            metadata_resolution_queued_count = 0
            metadata_resolution_deduplicated_count = 0
            if self.auto_enqueue_metadata_resolution:
                for media, identity_input_changed in resolution_candidates:
                    if media.discovery_status.casefold() != "missing":
                        continue
                    if await identity_finalization_evidence(session, media) is not None:
                        continue
                    if identity_input_changed:
                        cancelled_count = await cancel_active_metadata_resolution_jobs(
                            session,
                            media.id,
                            evidence="DISCOVERY_IDENTITY_INPUT_CHANGED",
                        )
                        if cancelled_count:
                            await session.flush()
                    _, deduplicated = await enqueue_metadata_resolution(
                        session,
                        media,
                        max_attempts=job.max_attempts,
                    )
                    if deduplicated:
                        metadata_resolution_deduplicated_count += 1
                    else:
                        metadata_resolution_queued_count += 1
            now = utc_now()
            job.status = JobStatus.SUCCEEDED
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.error_code = None
            job.error_message = None
            run.status = JobStatus.SUCCEEDED
            run.finished_at = now
            run.discovered_count = len(unique_items)
            run.created_count = created_count
            run.updated_count = updated_count
            run.error_code = None
            run.error_message = None
            session.add(
                AuditEvent(
                    event_type="DISCOVERY_RUN_SUCCEEDED",
                    entity_type="discovery_run",
                    entity_id=run.id,
                    sanitized_details={
                        "discovered_count": len(unique_items),
                        "created_count": created_count,
                        "updated_count": updated_count,
                        "archived_count": len(archived_items),
                        "restored_count": restored_count,
                        "isolated_warning_count": len(warnings),
                        "metadata_resolution_queued_count": (metadata_resolution_queued_count),
                        "metadata_resolution_deduplicated_count": (
                            metadata_resolution_deduplicated_count
                        ),
                    },
                )
            )
            await session.commit()

    async def _fail(self, job_id: str, error: AppError, *, lease_token: str | None) -> None:
        async with self.session_factory() as session:
            unlocked_job = await session.get(Job, job_id)
            media_id = unlocked_job.payload.get("media_id") if unlocked_job is not None else None
            metadata_job = bool(
                unlocked_job is not None
                and unlocked_job.job_type.startswith("RESOLVE_METADATA:")
                and isinstance(media_id, str)
            )
            media = (
                await session.get(MediaItem, media_id, with_for_update=True)
                if metadata_job
                else None
            )
            job = await session.scalar(
                select(Job).where(Job.id == job_id).with_for_update().limit(1)
            )
            if job is None or not self._owns_lease(job, lease_token):
                return
            if metadata_job and media is not None:
                evidence = await identity_finalization_evidence(session, media)
                if evidence is not None:
                    cancel_metadata_resolution_job(
                        session,
                        job,
                        media_id=media.id,
                        evidence=evidence,
                    )
                    await session.commit()
                    return
            retry = error.retryable and job.attempts < job.max_attempts
            retry_delay = self._retry_delay_seconds(error, job.attempts) if retry else None
            status = JobStatus.RETRY_WAIT if retry else JobStatus.FAILED
            job.status = status
            job.error_code = error.error_code
            job.error_message = error.message
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.next_retry_at = (
                utc_now() + timedelta(seconds=retry_delay) if retry_delay is not None else None
            )
            run = await session.get(DiscoveryRun, job.run_id) if job.run_id else None
            if run is not None:
                run.status = status
                run.error_code = error.error_code
                run.error_message = error.message
                run.finished_at = None if retry else utc_now()
                session.add(
                    AuditEvent(
                        event_type=(
                            "DISCOVERY_RUN_RETRY_SCHEDULED" if retry else "DISCOVERY_RUN_FAILED"
                        ),
                        entity_type="discovery_run",
                        entity_id=run.id,
                        sanitized_details=sanitize_details(
                            {
                                "error_code": error.error_code,
                                "message": error.message,
                                "retryable": retry,
                                "attempt": job.attempts,
                            }
                        ),
                    )
                )
            media_id = job.payload.get("media_id")
            if media is None and isinstance(media_id, str):
                media = await session.get(
                    MediaItem,
                    media_id,
                    with_for_update=job.job_type.startswith("TORRENT_SEARCH:"),
                )
            search_run_id = job.payload.get("search_run_id")
            search_run = (
                await session.get(TorrentSearchRun, search_run_id)
                if isinstance(search_run_id, str)
                else None
            )
            if job.job_type.startswith("RESOLVE_METADATA:") and media is not None:
                if not retry:
                    media.workflow_status = WorkflowStatus.IDENTITY_REVIEW
                    media.metadata_status = MetadataStatus.UNRESOLVED
                session.add(
                    AuditEvent(
                        event_type=(
                            "METADATA_RESOLUTION_RETRY_SCHEDULED"
                            if retry
                            else "METADATA_RESOLUTION_FAILED"
                        ),
                        entity_type="media_item",
                        entity_id=media.id,
                        sanitized_details={
                            "error_code": error.error_code,
                            "message": error.message,
                            "retryable": retry,
                        },
                    )
                )
            if job.job_type.startswith("TORRENT_SEARCH:") and search_run is not None:
                search_run.error_code = error.error_code
                search_run.error_message = error.message
                if not retry:
                    search_run.status = WorkflowStatus.SEARCH_FAILED
                    search_run.finished_at = utc_now()
                if media is not None:
                    await refresh_media_search_workflow_status(session, media)
                session.add(
                    AuditEvent(
                        event_type=(
                            "TORRENT_SEARCH_RETRY_SCHEDULED" if retry else "TORRENT_SEARCH_FAILED"
                        ),
                        entity_type="torrent_search_run",
                        entity_id=search_run.id,
                        sanitized_details={
                            "error_code": error.error_code,
                            "message": error.message,
                            "retryable": retry,
                            "site_id": search_run.site_id,
                        },
                    )
                )
            await session.commit()

    @staticmethod
    def _retry_delay_seconds(error: AppError, attempts: int) -> float:
        fallback = float(min(3600, 2**attempts))
        value = error.details.get("retry_after_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return fallback
        requested = float(value)
        if not math.isfinite(requested):
            return fallback
        return max(fallback, min(3600.0, max(0.0, requested)))
