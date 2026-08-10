from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import MediaSourceAdapter, MetadataProvider, PtSiteAdapter
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
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.schemas.entities import TorrentSearchCreateRequest
from app.services.matching import MatchPreferences, score_metadata_match, score_torrent_candidate


class JobProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        adapter_factory: Callable[[], MediaSourceAdapter],
        metadata_provider_factory: Callable[[], MetadataProvider] | None = None,
        pt_site_factory: Callable[[], PtSiteAdapter] | None = None,
        *,
        lease_seconds: int = 300,
        lease_renew_interval_seconds: float = 60,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not 0 < lease_renew_interval_seconds < lease_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease")
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.adapter_factory = adapter_factory
        self.metadata_provider_factory = metadata_provider_factory
        self.pt_site_factory = pt_site_factory
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = lease_renew_interval_seconds

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
                .where(claimable, Job.attempts < Job.max_attempts)
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
            if isinstance(media_id, str):
                media = await session.get(MediaItem, media_id)
                if media is not None and job.job_type.startswith("RESOLVE_METADATA:"):
                    media.workflow_status = WorkflowStatus.METADATA_PENDING
                if media is not None and job.job_type.startswith("TORRENT_SEARCH:"):
                    media.workflow_status = WorkflowStatus.PT_SEARCHING
            search_run_id = job.payload.get("search_run_id")
            if isinstance(search_run_id, str):
                search_run = await session.get(TorrentSearchRun, search_run_id)
                if search_run is not None:
                    search_run.status = WorkflowStatus.PT_SEARCHING
                    search_run.started_at = search_run.started_at or now
            await session.commit()
            return job

    async def run_once(self) -> bool:
        await self.heartbeat()
        job = await self.claim_job()
        if job is None:
            return False
        stop_renewal = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew_lease_loop(job.id, job.lease_token, stop_renewal)
        )
        try:
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
        finally:
            stop_renewal.set()
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal

    async def _renew_lease_loop(
        self, job_id: str, lease_token: str | None, stop: asyncio.Event
    ) -> None:
        if lease_token is None:
            return
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.lease_renew_interval_seconds
                )
                return
            except TimeoutError:
                if not await self.renew_lease(job_id, lease_token):
                    return

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
            if self.metadata_provider_factory is None:
                raise AppError("TMDB_LIVE_DISABLED", "TMDB 真实只读 Provider 未启用")
            media_id = job.payload.get("media_id")
            if not isinstance(media_id, str):
                raise AppError("INVALID_JOB_PAYLOAD", "元数据任务参数无效")
            async with self.session_factory() as session:
                media = await session.get(MediaItem, media_id)
                if media is None:
                    raise AppError("MEDIA_NOT_FOUND", "影视条目不存在", status_code=404)
            provider = self.metadata_provider_factory()
            candidates: list[MetadataRecord]
            if media.tmdb_id is not None:
                try:
                    candidates = [await provider.get_by_tmdb_id(media.media_type, media.tmdb_id)]
                except AppError as exc:
                    if exc.error_code != "TMDB_NOT_FOUND":
                        raise
                    opposite = (
                        MediaType.TV
                        if media.media_type == MediaType.MOVIE
                        else MediaType.MOVIE
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
                job.id, media.id, candidates, lease_token=job.lease_token
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

    async def _complete_metadata_resolution(
        self,
        job_id: str,
        media_id: str,
        candidates: list[MetadataRecord],
        *,
        lease_token: str | None,
    ) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            media = await session.get(MediaItem, media_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token) or media is None:
                return
            unique: dict[int, MetadataRecord] = {
                candidate.tmdb_id: candidate for candidate in candidates
            }
            ranked = sorted(
                (
                    (
                        candidate,
                        score_metadata_match(
                            media, candidate, expected_tmdb_id=media.tmdb_id
                        ),
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
            for rank, (candidate, match) in enumerate(ranked, start=1):
                session.add(
                    MetadataMatch(
                        media_id=media.id,
                        tmdb_id=candidate.tmdb_id,
                        rank=rank,
                        score=match.score,
                        match_reasons=match.reasons,
                        conflicts=match.conflicts,
                        candidate_snapshot=candidate.model_dump(mode="json"),
                    )
                )
            job.status = JobStatus.SUCCEEDED
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.error_code = None
            job.error_message = None
            media.workflow_status = WorkflowStatus.IDENTITY_REVIEW
            media.metadata_status = MetadataStatus.NEEDS_CONFIRMATION
            session.add(
                AuditEvent(
                    event_type="METADATA_CANDIDATES_READY",
                    entity_type="media_item",
                    entity_id=media.id,
                    sanitized_details={
                        "candidate_count": len(ranked),
                        "used_exact_tmdb_id": media.tmdb_id is not None,
                        "manual_confirmation_required": True,
                    },
                )
            )
            await session.commit()

    async def _run_torrent_search(self, job: Job) -> None:
        adapter: PtSiteAdapter | None = None
        try:
            if self.pt_site_factory is None:
                raise AppError("AVISTAZ_LIVE_DISABLED", "AvistaZ 真实只读搜索未启用")
            media_id = job.payload.get("media_id")
            search_run_id = job.payload.get("search_run_id")
            if not isinstance(media_id, str) or not isinstance(search_run_id, str):
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
                if review is None:
                    raise AppError(
                        "IDENTITY_CONFIRMATION_REQUIRED", "影视身份尚未人工确认", status_code=409
                    )
                metadata = MetadataRecord.model_validate(review.candidate_snapshot)
                requested = TorrentSearchCreateRequest.model_validate(search_run.sanitized_request)
                prior_titles = await self._prior_candidate_titles(session, media_id)
            adapter = self.pt_site_factory()
            candidates, strategy_log = await self._search_with_fallbacks(
                adapter, metadata, requested
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

    @staticmethod
    async def _search_with_fallbacks(
        adapter: PtSiteAdapter,
        metadata: MetadataRecord,
        requested: TorrentSearchCreateRequest,
    ) -> tuple[list[TorrentCandidate], list[dict[str, object]]]:
        common: dict[str, Any] = {
            "type": metadata.media_type,
            "page": 1,
            "limit": 50,
            "video_quality": requested.preferred_resolutions,
            "subtitle": requested.preferred_subtitles,
        }
        strategies: list[tuple[str, TorrentSearchRequest]] = [
            ("TMDB_ID", TorrentSearchRequest(tmdb=metadata.tmdb_id, **common)),
        ]
        if metadata.imdb_id:
            strategies.append(
                ("IMDB_ID", TorrentSearchRequest(imdb=metadata.imdb_id, **common))
            )
        text_values = [
            ("ENGLISH_TITLE_YEAR", metadata.english_title),
            ("ORIGINAL_TITLE_YEAR", metadata.original_title),
            ("CHINESE_OR_ALIAS", metadata.chinese_title or next(iter(metadata.aliases), None)),
        ]
        seen_queries: set[str] = set()
        for name, value in text_values:
            if not value:
                continue
            query = f"{value} {metadata.year}" if metadata.year else value
            if query.casefold() in seen_queries:
                continue
            seen_queries.add(query.casefold())
            strategies.append((name, TorrentSearchRequest(search=query, **common)))
        log: list[dict[str, object]] = []
        for name, request in strategies:
            results = await adapter.search(request)
            log.append({"strategy": name, "candidate_count": len(results)})
            if results:
                return list(results), log
        return [], log

    @staticmethod
    async def _prior_candidate_titles(session: AsyncSession, media_id: str) -> set[str]:
        statement = (
            select(TorrentCandidateRecord.candidate_snapshot)
            .join(TorrentSearchRun, TorrentSearchRun.id == TorrentCandidateRecord.search_run_id)
            .where(TorrentSearchRun.media_id == media_id)
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
            media.workflow_status = target_status
            job.status = JobStatus.SUCCEEDED
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.error_code = None
            job.error_message = None
            session.add(
                AuditEvent(
                    event_type=(
                        "TORRENT_CANDIDATES_READY"
                        if candidates
                        else "TORRENT_SEARCH_NO_CANDIDATE"
                    ),
                    entity_type="torrent_search_run",
                    entity_id=run.id,
                    sanitized_details={
                        "candidate_count": len(candidates),
                        "strategies": strategy_log,
                        "read_only": True,
                    },
                )
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
                and any(
                    value is None
                    for value in (
                        item.local_episodes,
                        item.local_episode_matrix,
                        item.total_episodes,
                        item.aired_episodes,
                        item.missing_episodes,
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
                    session.add(MediaItem(**item.model_dump()))
                    created_count += 1
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    updated_count += 1
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
                        "isolated_warning_count": len(warnings),
                    },
                )
            )
            await session.commit()

    async def _fail(
        self, job_id: str, error: AppError, *, lease_token: str | None
    ) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None or not self._owns_lease(job, lease_token):
                return
            retry = error.retryable and job.attempts < job.max_attempts
            status = JobStatus.RETRY_WAIT if retry else JobStatus.FAILED
            job.status = status
            job.error_code = error.error_code
            job.error_message = error.message
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            job.next_retry_at = utc_now() + timedelta(seconds=2**job.attempts) if retry else None
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
            media = (
                await session.get(MediaItem, media_id)
                if isinstance(media_id, str)
                else None
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
                        media.workflow_status = WorkflowStatus.SEARCH_FAILED
                session.add(
                    AuditEvent(
                        event_type=(
                            "TORRENT_SEARCH_RETRY_SCHEDULED"
                            if retry
                            else "TORRENT_SEARCH_FAILED"
                        ),
                        entity_type="torrent_search_run",
                        entity_id=search_run.id,
                        sanitized_details={
                            "error_code": error.error_code,
                            "message": error.message,
                            "retryable": retry,
                        },
                    )
                )
            await session.commit()
