from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast

import bencodepy  # type: ignore[import-untyped]
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import MetadataProvider, PtSiteAdapter
from app.adapters.downloaders.qbittorrent import QbAddResult, QbittorrentAdapter
from app.adapters.pt_sites.avistaz import AvistaZMockAdapter
from app.core import automation_runner
from app.core.automation_runner import recover_interrupted_jobs
from app.core.time import utc_now
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    MetadataRecord,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.schemas.qbittorrent import QbTorrent
from app.simple import automation
from app.simple.automation import get_policy, retry_job, run_automation, run_dry_run, update_policy
from app.simple.integrations import submit_download, sync_download_statuses
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    AutomationRun,
    AutomationRunState,
    Download,
    DownloadState,
    LibraryMediaItem,
    MediaState,
    ReleaseCandidate,
    ReleaseSearch,
)
from app.simple.schemas import AutomationPolicyUpdate


class FailingSearchAdapter(AvistaZMockAdapter):
    def __init__(self, *, retryable: bool) -> None:
        super().__init__()
        self.retryable = retryable

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]:
        del request
        raise AppError(
            "PT_TEMPORARY_FAILURE" if self.retryable else "PT_CONFIGURATION_ERROR",
            "PT 搜索失败",
            retryable=self.retryable,
        )


class FoundQbTorrent:
    async def authenticate(self) -> None:
        return None

    async def list_torrents(self) -> list[QbTorrent]:
        return [
            QbTorrent(
                hash="a" * 40,
                name="Restart.Movie.1080p.WEB-DL",
                size=2_000_000,
                progress=0.5,
                ratio=0,
                state="downloading",
            )
        ]


class UniqueMetadataProvider(MetadataProvider):
    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            id="tmdb-test",
            name="TMDB test",
            adapter_type="metadata",
            version="test",
            enabled=True,
            mode="MOCK",
            description="test",
        )

    async def get_by_tmdb_id(self, media_type: MediaType, tmdb_id: int) -> MetadataRecord:
        return MetadataRecord(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title="Identified Movie",
            original_title="Identified Movie",
            year=2026,
        )

    async def search(
        self, media_type: MediaType, title: str, year: int | None = None
    ) -> list[MetadataRecord]:
        del title, year
        return [await self.get_by_tmdb_id(media_type, 550)]

    async def get_external_ids(self, media_type: MediaType, tmdb_id: int) -> dict[str, str]:
        del media_type, tmdb_id
        return {}

    async def get_country_codes(self, media_type: MediaType, tmdb_id: int) -> list[str] | None:
        del media_type, tmdb_id
        return None

    async def get_tv_episode_matrix(self, tmdb_id: int) -> dict[int, list[int]] | None:
        del tmdb_id
        return None


def movie_candidate(tmdb_id: int, torrent_id: str) -> TorrentCandidate:
    return TorrentCandidate(
        site_id="avistaz",
        torrent_id=torrent_id,
        release_title=f"Movie.{tmdb_id}.2026.1080p.WEB-DL",
        details_ref=f"avistaz:details:{torrent_id}",
        media_type=MediaType.MOVIE,
        tmdb_id=tmdb_id,
        resolution="1080p",
        source="WEB-DL",
        size_bytes=2_000_000,
        seeders=8,
        hit_and_run=False,
    )


def release_candidate(
    torrent_id: str,
    *,
    reasons: list[str] | None = None,
    resolution: str = "1080p",
    source: str = "WEB-DL",
    size_bytes: int = 20_000,
    seeders: int = 5,
    download_factor: float | None = 1,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        search_id="search",
        site_id="avistaz",
        torrent_id=torrent_id,
        title=f"Candidate.{torrent_id}",
        resolution=resolution,
        source=source,
        size_bytes=size_bytes,
        seeders=seeders,
        download_factor=download_factor,
        score=0.9,
        reasons=reasons or ["TMDB_ID_EXACT"],
        warnings=[],
    )


def torrent_with_size(size_bytes: int) -> tuple[bytes, str]:
    info = {
        b"length": size_bytes,
        b"name": b"Automation.Movie.2026.1080p.WEB-DL.mkv",
        b"piece length": 16_384,
        b"pieces": b"p" * 40,
    }
    payload = bencodepy.encode({b"info": info})
    return payload, hashlib.sha1(bencodepy.encode(info)).hexdigest()


class TorrentPayloadSource:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def fetch_torrent(self, _torrent_id: str) -> bytes:
        return self.payload

    async def aclose(self) -> None:
        return None


class GuardAwareQb:
    def __init__(
        self,
        *,
        before_guard: Callable[[], Awaitable[None]] | None = None,
        unknown_outcome: bool = False,
    ) -> None:
        self.before_guard = before_guard
        self.unknown_outcome = unknown_outcome
        self.add_calls = 0

    async def authenticate(self) -> None:
        return None

    async def add_torrent(self, _payload: bytes, **kwargs: object) -> QbAddResult:
        if self.before_guard is not None:
            await self.before_guard()
        guard = cast(Callable[[], Awaitable[None]], kwargs["write_guard"])
        await guard()
        self.add_calls += 1
        if self.unknown_outcome:
            raise AppError(
                "QB_ADD_OUTCOME_UNKNOWN",
                "qBittorrent 写入结果未知",
                status_code=504,
            )
        return QbAddResult(info_hash=str(kwargs["expected_info_hash"]), outcome="SUBMITTED")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_automation_is_disabled_by_default(session_factory) -> None:
    async with session_factory() as session:
        policy = await get_policy(session)
        assert policy.enabled is False
        assert policy.dry_run is True

        with pytest.raises(AppError) as caught:
            await run_dry_run(
                session,
                adapter_factory=lambda _site_id: AvistaZMockAdapter(),
            )
        assert caught.value.error_code == "AUTOMATION_DISABLED"


@pytest.mark.asyncio
async def test_recorded_automation_run_persists_success_and_progress(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="recorded-automation-success",
            media_type=MediaType.MOVIE,
            tmdb_id=499,
            title="Recorded Automation Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, minimum_score=0.5),
        )
        run = await automation.create_automation_run(session, trigger="manual")
        run_id = run.id

        result = await run_automation(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(499, "recorded-success")]
            ),
            run_record=run,
        )

        assert result == (run_id, 1, 1, 0)
        await session.refresh(run)
        assert run.state == AutomationRunState.SUCCEEDED
        assert run.created_count == 1
        assert run.succeeded_count == 1
        assert run.failed_count == 0
        assert run.deferred_count == 0
        assert run.started_at is not None
        assert run.finished_at is not None

    async with session_factory() as session:
        stored = await session.get(AutomationRun, run_id)
        assert stored is not None
        assert stored.state == AutomationRunState.SUCCEEDED
        assert (
            stored.created_count,
            stored.succeeded_count,
            stored.failed_count,
            stored.deferred_count,
        ) == (1, 1, 0, 0)


@pytest.mark.asyncio
async def test_recorded_run_is_failed_when_a_job_permanently_fails(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="recorded-automation-failure",
            media_type=MediaType.MOVIE,
            tmdb_id=498,
            title="Recorded Automation Failure",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        run = await automation.create_automation_run(session, trigger="manual")

        result = await run_automation(
            session,
            adapter_factory=lambda _site_id: FailingSearchAdapter(retryable=False),
            run_record=run,
        )

        assert result == (run.id, 1, 0, 1)
        assert run.state == AutomationRunState.FAILED
        assert run.created_count == 1
        assert run.succeeded_count == 0
        assert run.failed_count == 1
        assert run.deferred_count == 0
        assert run.error_message == "1 个任务执行失败"


@pytest.mark.asyncio
async def test_recorded_automation_run_persists_run_level_failure(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_run(*_args: object, **_kwargs: object) -> tuple[str, int, int, int]:
        raise AppError("PT_CONFIGURATION_ERROR", "PT 配置错误", status_code=409)

    monkeypatch.setattr(automation, "run_automation", fail_run)

    async with session_factory() as session:
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        run = await automation.create_automation_run(session, trigger="manual")
        run_id = run.id

        await automation_runner.execute_recorded_automation_run(session, run)

    async with session_factory() as session:
        stored = await session.get(AutomationRun, run_id)
        assert stored is not None
        assert stored.state == AutomationRunState.FAILED
        assert stored.error_message == "PT 配置错误"
        assert stored.finished_at is not None


@pytest.mark.asyncio
async def test_run_level_failure_releases_pending_jobs_for_retry(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_run(*_args: object, **_kwargs: object) -> tuple[str, int, int, int]:
        raise AppError("AUTOMATION_POLICY_CHANGED", "策略已变更", status_code=409)

    monkeypatch.setattr(automation, "run_automation", fail_run)

    async with session_factory() as session:
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        media = LibraryMediaItem(
            source_item_id="run-start-failure",
            media_type=MediaType.MOVIE,
            tmdb_id=498,
            title="Run Start Failure",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        run = await automation.create_automation_run(session, trigger="manual_retry")
        job = AutomationJob(
            run_id=run.id,
            media_id=media.id,
            state=AutomationJobState.PENDING,
            trigger="manual_retry",
        )
        session.add(job)
        await session.commit()

        await automation_runner.execute_recorded_automation_run(session, run)

        assert job.state == AutomationJobState.RETRY_WAIT
        assert job.next_attempt_at is not None
        assert run.state == AutomationRunState.FAILED
        assert run.created_count == 1
        assert run.deferred_count == 1


@pytest.mark.asyncio
async def test_manual_automation_run_recovers_when_initial_session_creation_fails(
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        await update_policy(session, AutomationPolicyUpdate(enabled=True))
        run = await automation.create_automation_run(session, trigger="manual")
        run_id = run.id

    class FailingSessionContext:
        async def __aenter__(self):
            raise RuntimeError("database connection failed")

        async def __aexit__(self, *_args: object) -> None:
            return None

    calls = 0

    def flaky_session_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            return FailingSessionContext()
        return session_factory()

    monkeypatch.setattr(automation_runner, "SessionFactory", flaky_session_factory)

    await automation_runner._execute_manual_automation_run(run_id)

    assert calls == 2
    async with session_factory() as session:
        stored = await session.get(AutomationRun, run_id)
        assert stored is not None
        assert stored.state == AutomationRunState.FAILED
        assert stored.error_message == "自动化后台任务启动失败"
        assert stored.finished_at is not None


@pytest.mark.asyncio
async def test_dry_run_searches_and_records_the_selected_candidate(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-movie",
            media_type=MediaType.MOVIE,
            tmdb_id=500,
            title="Automation Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                minimum_score=0.5,
                minimum_seeders=2,
            ),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="automation-release",
                    release_title="Automation.Movie.2026.1080p.WEB-DL",
                    details_ref="avistaz:details:automation-release",
                    media_type=MediaType.MOVIE,
                    tmdb_id=500,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=2_000_000,
                    seeders=8,
                    hit_and_run=False,
                )
            ]
        )

        run_id, created, succeeded, failed = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: adapter,
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run_id))
        assert job is not None
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.search_id is not None
        assert job.selected_candidate_id is not None
        assert job.decision["mode"] == "dry-run"
        assert job.decision["selected_title"] == "Automation.Movie.2026.1080p.WEB-DL"


@pytest.mark.asyncio
async def test_automation_region_scope_only_creates_jobs_for_matching_media(
    session_factory,
) -> None:
    async with session_factory() as session:
        korean = LibraryMediaItem(
            source_item_id="automation-korean",
            media_type=MediaType.MOVIE,
            tmdb_id=601,
            title="Korean Movie",
            country_codes=["KR"],
            state=MediaState.READY,
        )
        japanese = LibraryMediaItem(
            source_item_id="automation-japanese",
            media_type=MediaType.MOVIE,
            tmdb_id=602,
            title="Japanese Movie",
            country_codes=["JP"],
            state=MediaState.READY,
        )
        session.add_all([korean, japanese])
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, regions=["韩国"]),
        )

        run_id, created, _, _ = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(fixtures=[]),
        )

        assert created == 1
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run_id))
        assert job is not None
        assert job.media_id == korean.id


@pytest.mark.asyncio
async def test_automation_manual_scope_only_creates_jobs_for_selected_media(
    session_factory,
) -> None:
    async with session_factory() as session:
        selected = LibraryMediaItem(
            source_item_id="automation-selected",
            media_type=MediaType.TV,
            tmdb_id=701,
            title="Selected Show",
            country_codes=["KR"],
            state=MediaState.READY,
        )
        unselected = LibraryMediaItem(
            source_item_id="automation-unselected",
            media_type=MediaType.TV,
            tmdb_id=702,
            title="Unselected Show",
            country_codes=["KR"],
            state=MediaState.READY,
        )
        session.add_all([selected, unselected])
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                scope_mode="selected",
                selected_media_ids=[selected.id],
            ),
        )

        run_id, created, _, _ = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(fixtures=[]),
        )

        assert created == 1
        job = await session.scalar(select(AutomationJob).where(AutomationJob.run_id == run_id))
        assert job is not None
        assert job.media_id == selected.id


@pytest.mark.asyncio
async def test_dry_run_uses_exact_title_and_year_when_the_site_has_no_external_id(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-title-fallback",
            media_type=MediaType.MOVIE,
            tmdb_id=10,
            title="快乐的结局",
            original_title="Happy Ending",
            search_titles=["Happy Ending", "快乐的结局"],
            year=2012,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                minimum_score=0.5,
                minimum_seeders=1,
            ),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="wrong-longer-title",
                    release_title="My.Happy.Ending.2012.2160p.WEB-DL",
                    details_ref="avistaz:details:wrong-longer-title",
                    media_type=MediaType.MOVIE,
                    year=2012,
                    resolution="2160p",
                    source="WEB-DL",
                    size_bytes=80_000,
                    seeders=8,
                    hit_and_run=False,
                ),
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="correct-title",
                    release_title="Happy.Ending.2012.1080p.WEB-DL",
                    details_ref="avistaz:details:correct-title",
                    media_type=MediaType.MOVIE,
                    year=2012,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=40_000,
                    seeders=3,
                    hit_and_run=False,
                ),
            ]
        )

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.decision["selected_title"] == "Happy.Ending.2012.1080p.WEB-DL"
        rejected = cast(list[dict[str, object]], job.decision["rejected"])
        assert any(
            item["title"] == "My.Happy.Ending.2012.2160p.WEB-DL"
            and "缺少精确 ID，且资源标题未与影视名称或别名精确匹配"
            in cast(list[str], item["reasons"])
            for item in rejected
        )


@pytest.mark.asyncio
async def test_dry_run_searches_by_imdb_identity_when_tmdb_has_no_results(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-imdb-search",
            media_type=MediaType.MOVIE,
            tmdb_id=10,
            imdb_id="tt0010",
            title="Identity Movie",
            year=2012,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                minimum_score=0.5,
                minimum_seeders=1,
            ),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="imdb-only",
                    release_title="Identity.Movie.2012.1080p.WEB-DL",
                    details_ref="avistaz:details:imdb-only",
                    media_type=MediaType.MOVIE,
                    imdb_id="tt0010",
                    year=2012,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=40_000,
                    seeders=3,
                    hit_and_run=False,
                )
            ]
        )

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.decision["selected_title"] == "Identity.Movie.2012.1080p.WEB-DL"
        candidate = await session.get(ReleaseCandidate, job.selected_candidate_id)
        assert candidate is not None
        assert "IMDB_ID_EXACT" in candidate.reasons


@pytest.mark.asyncio
async def test_dry_run_explains_why_a_candidate_was_rejected(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-rejected",
            media_type=MediaType.MOVIE,
            tmdb_id=501,
            title="Rejected Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, minimum_score=0.9, minimum_seeders=10),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="weak-release",
                    release_title="Rejected.Movie.1080p.WEB-DL",
                    details_ref="avistaz:details:weak-release",
                    media_type=MediaType.MOVIE,
                    tmdb_id=501,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=2_000_000,
                    seeders=1,
                    hit_and_run=False,
                )
            ]
        )

        await run_dry_run(session, adapter_factory=lambda _site_id: adapter)

        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.selected_candidate_id is None
        rejected = job.decision["rejected"]
        assert isinstance(rejected, list)
        reasons = rejected[0]["reasons"]
        assert "评分低于策略门槛" in reasons
        assert "做种数不足" in reasons


@pytest.mark.asyncio
async def test_retryable_failure_waits_until_due_and_reuses_the_job(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=510,
            title="Retry Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, minimum_score=0.5, retry_delay_minutes=30),
        )

        _, created, succeeded, failed = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: FailingSearchAdapter(retryable=True),
        )
        assert (created, succeeded, failed) == (1, 0, 0)
        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.state == AutomationJobState.RETRY_WAIT
        assert job.attempt_count == 1

        _, created, _, _ = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(510, "retry-success")]
            ),
        )
        assert created == 0

        job.next_attempt_at = utc_now() - timedelta(seconds=1)
        await session.commit()
        _, created, succeeded, failed = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(510, "retry-success")]
            ),
        )
        assert (created, succeeded, failed) == (1, 1, 0)
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.attempt_count == 2


@pytest.mark.asyncio
async def test_non_retryable_failure_requires_explicit_retry(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-failed",
            media_type=MediaType.MOVIE,
            tmdb_id=511,
            title="Failed Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(session, AutomationPolicyUpdate(enabled=True))

        await run_dry_run(
            session,
            adapter_factory=lambda _site_id: FailingSearchAdapter(retryable=False),
        )
        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.state == AutomationJobState.FAILED

        _, created, _, _ = await run_dry_run(
            session, adapter_factory=lambda _site_id: AvistaZMockAdapter()
        )
        assert created == 0
        unrelated = LibraryMediaItem(
            source_item_id="automation-unrelated-to-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=513,
            title="Unrelated Movie",
            state=MediaState.READY,
        )
        session.add(unrelated)
        await session.commit()
        last_full_run_at = (await get_policy(session)).last_run_at
        retry, run = await retry_job(session, job.id)
        assert job.state == AutomationJobState.FAILED
        assert job.superseded_at is not None
        assert retry.state == AutomationJobState.PENDING
        assert retry.attempt_count == 0
        assert retry.retry_of_job_id == job.id
        assert retry.run_id == run.id

        result = await run_automation(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(511, "manual-retry-success")]
            ),
            run_record=run,
        )
        run_jobs = list(
            await session.scalars(select(AutomationJob).where(AutomationJob.run_id == run.id))
        )
        unrelated_job = await session.scalar(
            select(AutomationJob).where(AutomationJob.media_id == unrelated.id)
        )
        assert result == (run.id, 1, 1, 0)
        assert run_jobs == [retry]
        assert unrelated_job is None
        assert (await get_policy(session)).last_run_at == last_full_run_at


@pytest.mark.asyncio
async def test_due_retry_is_copied_into_the_new_run_without_moving_history(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-run-retry-lineage",
            media_type=MediaType.MOVIE,
            tmdb_id=512,
            title="Run Retry Lineage",
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, minimum_score=0.5),
        )

        first_run = await automation.create_automation_run(session, trigger="scheduled")
        await run_automation(
            session,
            adapter_factory=lambda _site_id: FailingSearchAdapter(retryable=True),
            run_record=first_run,
        )
        first_job = await session.scalar(
            select(AutomationJob).where(AutomationJob.run_id == first_run.id)
        )
        assert first_job is not None
        assert first_job.state == AutomationJobState.RETRY_WAIT
        assert first_run.deferred_count == 1
        assert first_run.failed_count == 0
        first_job.next_attempt_at = utc_now() - timedelta(seconds=1)
        await session.commit()
        first_next_attempt_at = first_job.next_attempt_at

        second_run = await automation.create_automation_run(session, trigger="scheduled")
        await run_automation(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(512, "lineage-success")]
            ),
            run_record=second_run,
        )
        second_job = await session.scalar(
            select(AutomationJob).where(AutomationJob.run_id == second_run.id)
        )

        assert second_job is not None
        assert second_job.retry_of_job_id == first_job.id
        assert second_job.state == AutomationJobState.SUCCEEDED
        assert first_job.run_id == first_run.id
        assert first_job.state == AutomationJobState.RETRY_WAIT
        assert first_job.next_attempt_at == first_next_attempt_at
        assert first_job.superseded_at is not None
        assert second_run.created_count == 1
        assert second_run.succeeded_count == 1


@pytest.mark.asyncio
async def test_live_mode_submits_once_then_defers_at_the_daily_limit(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        first = LibraryMediaItem(
            source_item_id="automation-live-1",
            media_type=MediaType.MOVIE,
            tmdb_id=520,
            title="Live Movie One",
            state=MediaState.READY,
        )
        session.add(first)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
                daily_download_limit=1,
            ),
        )
        submitted: list[str] = []

        async def fake_submit(
            download_session: AsyncSession, *, candidate_id: str, **_kwargs: object
        ) -> Download:
            candidate = await download_session.get(ReleaseCandidate, candidate_id)
            assert candidate is not None
            search = await download_session.get(ReleaseSearch, candidate.search_id)
            assert search is not None
            download = Download(
                media_id=search.media_id,
                candidate_id=candidate.id,
                name=candidate.title,
                state=DownloadState.QUEUED,
                submitted_at=utc_now(),
            )
            download_session.add(download)
            media = await download_session.get(LibraryMediaItem, search.media_id)
            assert media is not None
            media.state = MediaState.DOWNLOADING
            await download_session.commit()
            callback = _kwargs.get("on_download")
            assert callable(callback)
            await callback(download)
            submitted.append(candidate_id)
            return download

        monkeypatch.setattr(automation, "submit_download", fake_submit)
        adapter = AvistaZMockAdapter(fixtures=[movie_candidate(520, "live-one")])
        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )
        assert (created, succeeded, failed) == (1, 1, 0)
        assert len(submitted) == 1

        second = LibraryMediaItem(
            source_item_id="automation-live-2",
            media_type=MediaType.MOVIE,
            tmdb_id=521,
            title="Live Movie Two",
            state=MediaState.READY,
        )
        session.add(second)
        await session.commit()
        adapter.fixtures.append(movie_candidate(521, "live-two"))
        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )

        assert (created, succeeded, failed) == (1, 0, 0)
        assert len(submitted) == 1
        deferred = await session.scalar(
            select(AutomationJob).where(AutomationJob.media_id == second.id)
        )
        assert deferred is not None
        assert deferred.state == AutomationJobState.RETRY_WAIT
        assert deferred.decision["download_skipped"] == "已达到每日自动下载数量上限"


@pytest.mark.asyncio
async def test_daily_budget_uses_the_latest_submission_time_and_keeps_errors_counted(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-resubmitted-today",
            media_type=MediaType.MOVIE,
            tmdb_id=525,
            title="Resubmitted Today",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        previous = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="resubmitted-today",
            title="Resubmitted.Today.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(previous)
        await session.flush()
        download = Download(
            media_id=media.id,
            candidate_id=previous.id,
            name=previous.title,
            state=DownloadState.ERROR,
            content_size_bytes=20_000,
            created_at=utc_now() - timedelta(days=1),
            submitted_at=utc_now(),
        )
        session.add(download)
        await session.flush()
        job = AutomationJob(
            run_id="resubmitted-today-run",
            media_id=media.id,
            selected_candidate_id=previous.id,
            download_id=download.id,
            state=AutomationJobState.FAILED,
        )
        session.add(job)
        await session.commit()
        policy = await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                daily_download_limit=1,
            ),
        )
        next_candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="next-candidate",
            title="Next.Candidate.1080p.WEB-DL",
            size_bytes=10_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )

        reason = await automation._budget_reason(session, policy, next_candidate)

        assert reason == "已达到每日自动下载数量上限"
        retry_reason = await automation._budget_reason(
            session,
            policy,
            previous,
            exclude_download_id=download.id,
        )
        assert retry_reason is None


@pytest.mark.asyncio
async def test_restart_marks_an_inflight_qb_write_as_unknown(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-restart",
            media_type=MediaType.MOVIE,
            tmdb_id=530,
            title="Restart Movie",
            state=MediaState.DOWNLOADING,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="restart-release",
            title="Restart.Movie.1080p.WEB-DL",
            size_bytes=2_000_000,
            seeders=5,
            score=0.9,
        )
        session.add(candidate)
        await session.flush()
        download = Download(
            media_id=media.id,
            candidate_id=candidate.id,
            info_hash="a" * 40,
            name=candidate.title,
            state=DownloadState.SUBMITTING,
            submitted_at=utc_now(),
        )
        session.add(download)
        await session.flush()
        run = AutomationRun(
            id="restart-run",
            trigger="manual",
            state=AutomationRunState.RUNNING,
            created_count=1,
        )
        session.add(run)
        await session.flush()
        job = AutomationJob(
            run_id="restart-run",
            media_id=media.id,
            selected_candidate_id=candidate.id,
            download_id=download.id,
            state=AutomationJobState.RUNNING,
        )
        session.add(job)
        await session.commit()

        assert await recover_interrupted_jobs(session) == 2
        assert download.state == DownloadState.OUTCOME_UNKNOWN
        assert job.state == AutomationJobState.FAILED
        assert job.next_attempt_at is None
        assert run.state == AutomationRunState.FAILED
        assert run.failed_count == 1

        await sync_download_statuses(session, cast(QbittorrentAdapter, FoundQbTorrent()))
        assert download.state == DownloadState.DOWNLOADING
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.error_message is None
        assert run.state == AutomationRunState.SUCCEEDED
        assert run.succeeded_count == 1
        assert run.failed_count == 0


@pytest.mark.asyncio
async def test_restart_before_qb_add_is_safe_to_retry(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-restart-before-add",
            media_type=MediaType.MOVIE,
            tmdb_id=531,
            title="Restart Before Add",
            state=MediaState.DOWNLOADING,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="restart-before-add-release",
            title="Restart.Before.Add.1080p.WEB-DL",
            size_bytes=2_000_000,
            seeders=5,
            score=0.9,
        )
        session.add(candidate)
        await session.flush()
        download = Download(
            media_id=media.id,
            candidate_id=candidate.id,
            info_hash="b" * 40,
            name=candidate.title,
            state=DownloadState.SUBMITTING,
            submitted_at=None,
        )
        session.add(download)
        await session.flush()
        job = AutomationJob(
            run_id="restart-before-add-run",
            media_id=media.id,
            selected_candidate_id=candidate.id,
            download_id=download.id,
            state=AutomationJobState.RUNNING,
        )
        session.add(job)
        await session.commit()

        assert await recover_interrupted_jobs(session) == 1
        assert download.state == DownloadState.ERROR
        assert download.error_message == "应用在 qBittorrent 写入前重启，等待安全重试"
        assert job.state == AutomationJobState.RETRY_WAIT
        assert job.next_attempt_at is not None


@pytest.mark.asyncio
async def test_restart_marks_inflight_automation_runs_failed(session_factory) -> None:
    async with session_factory() as session:
        pending = AutomationRun(trigger="manual", state=AutomationRunState.PENDING)
        running = AutomationRun(trigger="scheduled", state=AutomationRunState.RUNNING)
        session.add_all([pending, running])
        await session.commit()

        assert await recover_interrupted_jobs(session) == 2
        for run in (pending, running):
            assert run.state == AutomationRunState.FAILED
            assert run.error_message == "应用重启中断了本次自动化运行"
            assert run.finished_at is not None


@pytest.mark.asyncio
async def test_restart_makes_a_pending_retry_job_retryable_and_updates_counts(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="pending-retry-restart",
            media_type=MediaType.MOVIE,
            tmdb_id=532,
            title="Pending Retry Restart",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        run = AutomationRun(trigger="manual_retry", state=AutomationRunState.PENDING)
        session.add(run)
        await session.flush()
        job = AutomationJob(
            run_id=run.id,
            media_id=media.id,
            state=AutomationJobState.PENDING,
            trigger="manual_retry",
        )
        session.add(job)
        await session.commit()

        assert await recover_interrupted_jobs(session) == 2
        assert job.state == AutomationJobState.RETRY_WAIT
        assert job.next_attempt_at is not None
        assert job.finished_at is not None
        assert run.state == AutomationRunState.FAILED
        assert run.created_count == 1
        assert run.succeeded_count == 0
        assert run.failed_count == 0
        assert run.deferred_count == 1


@pytest.mark.asyncio
async def test_restart_respects_the_maximum_attempt_count(session_factory) -> None:
    async with session_factory() as session:
        await update_policy(
            session,
            AutomationPolicyUpdate(enabled=True, max_attempts=1),
        )
        media = LibraryMediaItem(
            source_item_id="exhausted-restart",
            media_type=MediaType.MOVIE,
            tmdb_id=533,
            title="Exhausted Restart",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        run = AutomationRun(trigger="manual", state=AutomationRunState.RUNNING)
        session.add(run)
        await session.flush()
        job = AutomationJob(
            run_id=run.id,
            media_id=media.id,
            state=AutomationJobState.RUNNING,
            attempt_count=1,
        )
        session.add(job)
        await session.commit()

        assert await recover_interrupted_jobs(session) == 2
        assert job.state == AutomationJobState.FAILED
        assert job.next_attempt_at is None
        assert run.state == AutomationRunState.FAILED
        assert run.failed_count == 1


@pytest.mark.asyncio
async def test_qb_reconciliation_keeps_superseded_history_immutable_and_updates_run(
    session_factory,
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="reconcile-lineage",
            media_type=MediaType.MOVIE,
            tmdb_id=534,
            title="Reconcile Lineage",
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="reconcile-lineage",
            title="Restart.Movie.1080p.WEB-DL",
            size_bytes=2_000_000,
            seeders=5,
            score=0.9,
        )
        session.add(candidate)
        await session.flush()
        download = Download(
            media_id=media.id,
            candidate_id=candidate.id,
            info_hash="a" * 40,
            name=candidate.title,
            state=DownloadState.OUTCOME_UNKNOWN,
        )
        session.add(download)
        old_run = AutomationRun(
            trigger="manual",
            state=AutomationRunState.FAILED,
            created_count=1,
            failed_count=1,
        )
        current_run = AutomationRun(
            trigger="manual_retry",
            state=AutomationRunState.FAILED,
            created_count=1,
            failed_count=1,
        )
        session.add_all([old_run, current_run])
        await session.flush()
        old_job = AutomationJob(
            run_id=old_run.id,
            media_id=media.id,
            download_id=download.id,
            state=AutomationJobState.FAILED,
            superseded_at=utc_now(),
        )
        session.add(old_job)
        await session.flush()
        current_job = AutomationJob(
            run_id=current_run.id,
            media_id=media.id,
            download_id=download.id,
            state=AutomationJobState.FAILED,
            retry_of_job_id=old_job.id,
        )
        session.add(current_job)
        await session.commit()

        await sync_download_statuses(session, cast(QbittorrentAdapter, FoundQbTorrent()))

        assert old_job.state == AutomationJobState.FAILED
        assert old_run.state == AutomationRunState.FAILED
        assert old_run.failed_count == 1
        assert current_job.state == AutomationJobState.SUCCEEDED
        assert current_run.state == AutomationRunState.SUCCEEDED
        assert current_run.succeeded_count == 1
        assert current_run.failed_count == 0


def test_live_mode_never_accepts_identity_or_partial_pack_warnings() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        allow_warnings=True,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = ReleaseCandidate(
        search_id="search",
        site_id="avistaz",
        torrent_id="unsafe",
        title="Wrong.Movie.1080p.WEB-DL",
        size_bytes=2_000_000,
        seeders=8,
        score=0.9,
        warnings=["ID_MISMATCH", "PARTIAL_PACK"],
    )

    selected, rejected = automation._choose_candidate(
        policy, [candidate], media_type=MediaType.MOVIE
    )

    assert selected is None
    assert rejected[0]["reasons"] == [
        "站点提供的 TMDB 或 IMDb 与目标影视不匹配",
        "资源不是完整资源包",
    ]


def test_live_mode_accepts_a_verified_title_year_fallback() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        site_ids=["avistaz"],
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = ReleaseCandidate(
        search_id="search",
        site_id="avistaz",
        torrent_id="text-only-match",
        title="Similar.Movie.2026.1080p.WEB-DL",
        size_bytes=2_000_000,
        seeders=8,
        score=0.9,
        reasons=["TITLE_EXACT", "YEAR_MATCH", "MEDIA_TYPE_MATCH"],
        warnings=[],
    )

    selected, rejected = automation._choose_candidate(
        policy, [candidate], media_type=MediaType.MOVIE
    )

    assert selected is candidate
    assert rejected == []


def test_exact_tmdb_identity_is_not_rejected_by_unverifiable_auxiliary_metadata() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = release_candidate("exact-with-auxiliary-warnings")
    candidate.warnings = ["ID_UNVERIFIED", "YEAR_MISMATCH"]

    selected, rejected = automation._choose_candidate(
        policy, [candidate], media_type=MediaType.MOVIE
    )

    assert selected is candidate
    assert rejected == []


def test_title_fallback_requires_title_year_and_media_type_evidence() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = release_candidate(
        "incomplete-fallback",
        reasons=["TITLE_EXACT", "MEDIA_TYPE_MATCH"],
    )

    selected, rejected = automation._choose_candidate(
        policy, [candidate], media_type=MediaType.MOVIE
    )

    assert selected is None
    assert rejected[0]["reasons"] == ["缺少精确 ID，且无法确认资源年份匹配"]


def test_tv_candidates_require_a_complete_series_or_season_pack() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    single_episode = release_candidate("single-episode")
    single_episode.season_coverage = [1]
    single_episode.episode_coverage = ["S01E01"]
    covered = release_candidate("covered", reasons=["TMDB_ID_EXACT", "TV_COMPLETE_SEASON_PACK"])
    covered.collection_type = "season"
    covered.season_coverage = [1]

    selected, rejected = automation._choose_candidate(
        policy,
        [single_episode, covered],
        media_type=MediaType.TV,
    )

    assert selected is covered
    assert rejected[0]["reasons"] == ["无法确认资源为全集包或完整季包"]


@pytest.mark.parametrize(
    "title",
    [
        "Candidate.S01.E01.1080p.WEB-DL",
        "Candidate.S01.Special.1080p.WEB-DL",
        "Candidate.Complete.Series.Trailer.1080p.WEB-DL",
        "Candidate.Incomplete.Season.1.1080p.WEB-DL",
    ],
)
def test_live_mode_rejects_legacy_ambiguous_season_candidates(title: str) -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = release_candidate("legacy-ambiguous-season")
    candidate.title = title
    candidate.collection_type = "season"
    candidate.file_count = 2
    candidate.season_coverage = [1]

    selected, rejected = automation._choose_candidate(
        policy,
        [candidate],
        media_type=MediaType.TV,
    )

    assert selected is None
    assert rejected[0]["reasons"] == ["无法确认资源为全集包或完整季包"]


def test_live_mode_rejects_a_non_pack_title_even_with_a_stale_pack_reason() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = release_candidate(
        "stale-complete-series-trailer",
        reasons=["TMDB_ID_EXACT", "TV_COMPLETE_SERIES_PACK"],
    )
    candidate.title = "Candidate.Complete.Series.Trailer.1080p.WEB-DL"
    candidate.collection_type = "complete_series"
    candidate.file_count = 12

    selected, rejected = automation._choose_candidate(
        policy,
        [candidate],
        media_type=MediaType.TV,
    )

    assert selected is None
    assert rejected[0]["reasons"] == ["无法确认资源为全集包或完整季包"]


def test_live_mode_always_rejects_an_unverified_tv_pack_warning() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    candidate = release_candidate(
        "fullwidth-trailer",
        reasons=["TMDB_ID_EXACT", "TV_COMPLETE_SERIES_PACK"],
    )
    candidate.title = "Candidate.Complete.Series.ＴＲＡＩＬＥＲ.1080p.WEB-DL"
    candidate.collection_type = "complete_series"
    candidate.file_count = 12
    candidate.warnings = ["TV_PACK_UNVERIFIED"]

    selected, rejected = automation._choose_candidate(
        policy,
        [candidate],
        media_type=MediaType.TV,
    )

    assert selected is None
    assert rejected[0]["reasons"] == ["无法确认资源为全集包或完整季包"]


def test_tv_candidate_ranking_prefers_a_complete_series_over_one_season() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    season = release_candidate("season-pack", reasons=["TMDB_ID_EXACT", "TV_COMPLETE_SEASON_PACK"])
    season.collection_type = "season"
    season.season_coverage = [1]
    complete = release_candidate(
        "complete-series",
        reasons=["TMDB_ID_EXACT", "TV_COMPLETE_SERIES_PACK"],
    )
    complete.collection_type = "complete_series"

    selected, _ = automation._choose_candidate(policy, [season, complete], media_type=MediaType.TV)

    assert selected is complete


def test_candidate_ranking_prioritizes_identity_resolution_and_then_size() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    fallback_2160p = release_candidate(
        "fallback-2160p",
        reasons=["TITLE_EXACT", "YEAR_MATCH", "MEDIA_TYPE_MATCH"],
        resolution="2160p",
        size_bytes=80_000,
    )
    exact_1080p = release_candidate(
        "exact-1080p",
        resolution="1080p",
        size_bytes=100_000,
    )
    exact_2160p_small = release_candidate(
        "exact-2160p-small",
        resolution="2160p",
        size_bytes=40_000,
    )
    exact_2160p_large = release_candidate(
        "exact-2160p-large",
        resolution="2160p",
        size_bytes=60_000,
    )

    selected, rejected = automation._choose_candidate(
        policy,
        [fallback_2160p, exact_1080p, exact_2160p_small, exact_2160p_large],
        media_type=MediaType.MOVIE,
    )

    assert selected is exact_2160p_large
    assert len(rejected) == 3
    assert all(item["reasons"] == ["符合硬性条件，但综合排序低于已选资源"] for item in rejected)


def test_movie_ranking_ignores_tv_pack_labels() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    misleading_pack = release_candidate("movie-complete-series", size_bytes=40_000)
    misleading_pack.collection_type = "complete_series"
    regular = release_candidate("regular-movie", size_bytes=60_000)

    selected, _ = automation._choose_candidate(
        policy,
        [misleading_pack, regular],
        media_type=MediaType.MOVIE,
    )

    assert selected is regular


def test_candidate_ranking_avoids_a_fragile_single_seeder_before_size() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    fragile_large = release_candidate("fragile", size_bytes=100_000, seeders=1)
    healthy_smaller = release_candidate("healthy", size_bytes=80_000, seeders=3)

    selected, _ = automation._choose_candidate(
        policy,
        [fragile_large, healthy_smaller],
        media_type=MediaType.MOVIE,
    )

    assert selected is healthy_smaller


def test_candidate_ranking_applies_source_and_download_promotion_before_size() -> None:
    policy = automation.AutomationPolicy(
        enabled=True,
        dry_run=False,
        minimum_score=0.5,
        minimum_seeders=1,
    )
    bluray = release_candidate(
        "bluray",
        source="BluRay",
        size_bytes=40_000,
        download_factor=1,
    )
    free_web = release_candidate(
        "free-web",
        source="WEB-DL",
        size_bytes=100_000,
        download_factor=0,
    )
    selected, _ = automation._choose_candidate(
        policy, [free_web, bluray], media_type=MediaType.MOVIE
    )
    assert selected is bluray

    free_small = release_candidate(
        "free-small",
        source="BluRay",
        size_bytes=40_000,
        download_factor=0,
    )
    normal_large = release_candidate(
        "normal-large",
        source="BluRay",
        size_bytes=100_000,
        download_factor=1,
    )
    selected, _ = automation._choose_candidate(
        policy, [normal_large, free_small], media_type=MediaType.MOVIE
    )
    assert selected is free_small


@pytest.mark.asyncio
async def test_retry_rechecks_the_selected_candidate_against_the_current_policy(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-policy-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=551,
            title="Policy Retry Movie",
            state=MediaState.CANDIDATES,
        )
        session.add(media)
        await session.flush()
        search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(search)
        await session.flush()
        candidate = ReleaseCandidate(
            search_id=search.id,
            site_id="avistaz",
            torrent_id="old-selection",
            title="Policy.Retry.Movie.2026.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.75,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(candidate)
        await session.flush()
        job = AutomationJob(
            run_id="policy-retry-run",
            media_id=media.id,
            state=AutomationJobState.RETRY_WAIT,
            search_id=search.id,
            selected_candidate_id=candidate.id,
            next_attempt_at=utc_now() - timedelta(seconds=1),
            decision={"selected_title": candidate.title, "selected_score": candidate.score},
        )
        session.add(job)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.9,
            ),
        )

        async def unexpected_submit(*_args: object, **_kwargs: object) -> Download:
            raise AssertionError("不符合当前策略的旧候选不应进入下载提交")

        monkeypatch.setattr(automation, "submit_download", unexpected_submit)
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="old-selection",
                    release_title="Policy.Retry.Movie.2026.1080p.WEB-DL",
                    details_ref="avistaz:details:old-selection",
                    media_type=MediaType.MOVIE,
                    tmdb_id=551,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=20_000,
                    seeders=8,
                    hit_and_run=False,
                )
            ]
        )

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.selected_candidate_id is None
        rejected = cast(list[dict[str, object]], job.decision["rejected"])
        assert rejected[0]["reasons"] == ["评分低于策略门槛"]


@pytest.mark.asyncio
async def test_retry_refreshes_current_seeders_before_download_submission(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-seeder-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=552,
            title="Seeder Retry Movie",
            year=2026,
            state=MediaState.CANDIDATES,
        )
        session.add(media)
        await session.flush()
        old_search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(old_search)
        await session.flush()
        old_candidate = ReleaseCandidate(
            search_id=old_search.id,
            site_id="avistaz",
            torrent_id="seeder-retry",
            title="Seeder.Retry.Movie.2026.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(old_candidate)
        await session.flush()
        job = AutomationJob(
            run_id="seeder-retry-run",
            media_id=media.id,
            state=AutomationJobState.RETRY_WAIT,
            search_id=old_search.id,
            selected_candidate_id=old_candidate.id,
            next_attempt_at=utc_now() - timedelta(seconds=1),
            decision={"selected_title": old_candidate.title},
        )
        session.add(job)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
                minimum_seeders=1,
            ),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="seeder-retry",
                    release_title="Seeder.Retry.Movie.2026.1080p.WEB-DL",
                    details_ref="avistaz:details:seeder-retry",
                    media_type=MediaType.MOVIE,
                    tmdb_id=552,
                    year=2026,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=20_000,
                    seeders=0,
                    hit_and_run=False,
                )
            ]
        )

        async def unexpected_submit(*_args: object, **_kwargs: object) -> Download:
            raise AssertionError("已经没有做种的候选不应进入下载提交")

        monkeypatch.setattr(automation, "submit_download", unexpected_submit)

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.search_id != old_search.id
        assert job.selected_candidate_id is None
        rejected = cast(list[dict[str, object]], job.decision["rejected"])
        assert "当前没有做种，不能自动下载" in cast(list[str], rejected[0]["reasons"])


@pytest.mark.asyncio
async def test_retry_refreshes_the_same_torrent_and_reuses_its_failed_download(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-download-retry",
            media_type=MediaType.MOVIE,
            tmdb_id=553,
            title="Download Retry Movie",
            year=2026,
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.flush()
        old_search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(old_search)
        await session.flush()
        old_candidate = ReleaseCandidate(
            search_id=old_search.id,
            site_id="avistaz",
            torrent_id="same-torrent-retry",
            title="Download.Retry.Movie.2026.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=1,
            score=0.7,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
        )
        session.add(old_candidate)
        await session.flush()
        failed_download = Download(
            media_id=media.id,
            candidate_id=old_candidate.id,
            name=old_candidate.title,
            state=DownloadState.ERROR,
            error_message="temporary failure",
        )
        session.add(failed_download)
        job = AutomationJob(
            run_id="download-retry-run",
            media_id=media.id,
            state=AutomationJobState.RETRY_WAIT,
            search_id=old_search.id,
            selected_candidate_id=old_candidate.id,
            next_attempt_at=utc_now() - timedelta(seconds=1),
            decision={"selected_title": old_candidate.title},
        )
        session.add(job)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
                minimum_seeders=1,
            ),
        )
        adapter = AvistaZMockAdapter(
            fixtures=[
                TorrentCandidate(
                    site_id="avistaz",
                    torrent_id="same-torrent-retry",
                    release_title="Download.Retry.Movie.2026.1080p.WEB-DL",
                    details_ref="avistaz:details:same-torrent-retry",
                    media_type=MediaType.MOVIE,
                    tmdb_id=553,
                    year=2026,
                    resolution="1080p",
                    source="WEB-DL",
                    size_bytes=20_000,
                    seeders=6,
                    hit_and_run=False,
                )
            ]
        )

        async def fake_submit(
            download_session: AsyncSession, *, candidate_id: str, **kwargs: object
        ) -> Download:
            assert candidate_id == old_candidate.id
            assert job.download_id == failed_download.id
            failed_download.state = DownloadState.QUEUED
            failed_download.error_message = None
            failed_download.submitted_at = utc_now()
            await download_session.commit()
            callback = kwargs.get("on_download")
            assert callable(callback)
            await callback(failed_download)
            return failed_download

        monkeypatch.setattr(automation, "submit_download", fake_submit)

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: adapter,
            pt_factory=lambda _site_id: cast(PtSiteAdapter, object()),
            qb_factory=lambda: cast(QbittorrentAdapter, object()),
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert job.state == AutomationJobState.SUCCEEDED
        assert job.selected_candidate_id == old_candidate.id
        assert job.download_id == failed_download.id
        assert old_candidate.seeders == 6
        assert len(list(await session.scalars(select(Download)))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_limits", "expected_message"),
    [
        ({"max_size_bytes": 25_000}, "种子实际体积超过单资源上限"),
        ({"daily_download_bytes": 25_000}, "将超过每日自动下载体积上限"),
    ],
)
async def test_validated_torrent_size_blocks_qb_add_when_it_exceeds_the_policy(
    session_factory,
    policy_limits: dict[str, int],
    expected_message: str,
) -> None:
    payload, info_hash = torrent_with_size(30_000)
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id=f"automation-actual-size-{expected_message}",
            media_type=MediaType.MOVIE,
            tmdb_id=560,
            title="Actual Size Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
                **policy_limits,
            ),
        )
        candidate = TorrentCandidate(
            site_id="avistaz",
            torrent_id="actual-size-release",
            release_title="Actual.Size.Movie.2026.1080p.WEB-DL",
            details_ref="avistaz:details:actual-size-release",
            media_type=MediaType.MOVIE,
            tmdb_id=560,
            year=2026,
            resolution="1080p",
            source="WEB-DL",
            size_bytes=20_000,
            seeders=8,
            hit_and_run=False,
            info_hash=info_hash,
        )
        search_adapter = AvistaZMockAdapter(fixtures=[candidate])
        torrent_source = cast(PtSiteAdapter, TorrentPayloadSource(payload))
        qb_impl = GuardAwareQb()

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: search_adapter,
            pt_factory=lambda _site_id: torrent_source,
            qb_factory=lambda: cast(QbittorrentAdapter, qb_impl),
        )

        assert (created, succeeded, failed) == (1, 0, 1)
        assert qb_impl.add_calls == 0
        download = await session.scalar(select(Download))
        assert download is not None
        assert download.content_size_bytes == 30_000
        assert download.state == DownloadState.ERROR
        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.state == AutomationJobState.FAILED
        assert expected_message in (job.error_message or "")


@pytest.mark.asyncio
async def test_disabling_the_policy_before_qb_add_stops_the_write(session_factory) -> None:
    payload, info_hash = torrent_with_size(20_000)

    async def disable_policy() -> None:
        async with session_factory() as policy_session:
            policy = await get_policy(policy_session)
            policy.enabled = False
            await policy_session.commit()

    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-disable-before-add",
            media_type=MediaType.MOVIE,
            tmdb_id=561,
            title="Disable Before Add Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
            ),
        )
        candidate = TorrentCandidate(
            site_id="avistaz",
            torrent_id="disable-before-add-release",
            release_title="Disable.Before.Add.Movie.2026.1080p.WEB-DL",
            details_ref="avistaz:details:disable-before-add-release",
            media_type=MediaType.MOVIE,
            tmdb_id=561,
            year=2026,
            resolution="1080p",
            source="WEB-DL",
            size_bytes=20_000,
            seeders=8,
            hit_and_run=False,
            info_hash=info_hash,
        )
        search_adapter = AvistaZMockAdapter(fixtures=[candidate])
        torrent_source = cast(PtSiteAdapter, TorrentPayloadSource(payload))
        qb_impl = GuardAwareQb(before_guard=disable_policy)

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: search_adapter,
            pt_factory=lambda _site_id: torrent_source,
            qb_factory=lambda: cast(QbittorrentAdapter, qb_impl),
        )

        assert (created, succeeded, failed) == (1, 0, 1)
        assert qb_impl.add_calls == 0
        job = await session.scalar(select(AutomationJob))
        assert job is not None
        assert job.state == AutomationJobState.FAILED
        assert job.error_message == "自动化已暂停或切换为演练模式，已停止下载提交"


@pytest.mark.asyncio
async def test_unknown_qb_add_outcome_is_linked_and_never_submitted_twice(
    session_factory,
) -> None:
    payload, info_hash = torrent_with_size(20_000)
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-unknown-add",
            media_type=MediaType.MOVIE,
            tmdb_id=562,
            title="Unknown Add Movie",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
            ),
        )
        candidate = TorrentCandidate(
            site_id="avistaz",
            torrent_id="unknown-add-release",
            release_title="Unknown.Add.Movie.2026.1080p.WEB-DL",
            details_ref="avistaz:details:unknown-add-release",
            media_type=MediaType.MOVIE,
            tmdb_id=562,
            year=2026,
            resolution="1080p",
            source="WEB-DL",
            size_bytes=20_000,
            seeders=8,
            hit_and_run=False,
            info_hash=info_hash,
        )
        search_adapter = AvistaZMockAdapter(fixtures=[candidate])
        torrent_source = cast(PtSiteAdapter, TorrentPayloadSource(payload))
        qb_impl = GuardAwareQb(unknown_outcome=True)

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: search_adapter,
            pt_factory=lambda _site_id: torrent_source,
            qb_factory=lambda: cast(QbittorrentAdapter, qb_impl),
        )

        assert (created, succeeded, failed) == (1, 0, 1)
        assert qb_impl.add_calls == 1
        job = await session.scalar(select(AutomationJob))
        download = await session.scalar(select(Download))
        assert job is not None
        assert download is not None
        assert job.download_id == download.id
        assert download.state == DownloadState.OUTCOME_UNKNOWN
        assert job.state == AutomationJobState.FAILED

        retry, retry_run = await retry_job(session, job.id)
        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: search_adapter,
            pt_factory=lambda _site_id: torrent_source,
            qb_factory=lambda: cast(QbittorrentAdapter, qb_impl),
            run_record=retry_run,
        )

        assert (created, succeeded, failed) == (1, 0, 1)
        assert qb_impl.add_calls == 1
        assert job.state == AutomationJobState.FAILED
        assert job.superseded_at is not None
        assert retry.state == AutomationJobState.FAILED
        assert retry.error_message == "qBittorrent 写入结果未知，请等待状态对账"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing_state", "expected_message"),
    [
        (DownloadState.ERROR, "相同种子的已有下载记录处于错误状态"),
        (DownloadState.SUBMITTING, "相同种子的已有提交尚未完成"),
        (DownloadState.OUTCOME_UNKNOWN, "qBittorrent 写入结果未知"),
    ],
)
async def test_known_duplicate_hash_links_the_job_without_reporting_false_success(
    session_factory,
    existing_state: DownloadState,
    expected_message: str,
) -> None:
    _, info_hash = torrent_with_size(20_000)
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id=f"existing-duplicate-{existing_state.value}",
            media_type=MediaType.MOVIE,
            tmdb_id=571,
            title="Existing Duplicate",
            year=2026,
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        existing_search = ReleaseSearch(media_id=media.id, site_ids=["avistaz"])
        session.add(existing_search)
        await session.flush()
        existing_candidate = ReleaseCandidate(
            search_id=existing_search.id,
            site_id="avistaz",
            torrent_id=f"existing-{existing_state.value}",
            title="Existing.Duplicate.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
            info_hash=info_hash,
        )
        session.add(existing_candidate)
        await session.flush()
        existing_download = Download(
            media_id=media.id,
            candidate_id=existing_candidate.id,
            info_hash=info_hash,
            name=existing_candidate.title,
            state=existing_state,
        )
        session.add(existing_download)
        await session.commit()
        await update_policy(
            session,
            AutomationPolicyUpdate(
                enabled=True,
                dry_run=False,
                minimum_score=0.5,
            ),
        )
        duplicate_candidate = TorrentCandidate(
            site_id="avistaz",
            torrent_id=f"new-{existing_state.value}",
            release_title="New.Duplicate.2026.1080p.WEB-DL",
            details_ref=f"avistaz:details:new-{existing_state.value}",
            media_type=MediaType.MOVIE,
            tmdb_id=571,
            year=2026,
            resolution="1080p",
            source="WEB-DL",
            size_bytes=20_000,
            seeders=8,
            hit_and_run=False,
            info_hash=info_hash,
        )
        search_adapter = AvistaZMockAdapter(fixtures=[duplicate_candidate])

        def unexpected_pt(_site_id: str) -> PtSiteAdapter:
            raise AssertionError("已知重复 info hash 不应再次获取种子")

        def unexpected_qb() -> QbittorrentAdapter:
            raise AssertionError("已知重复 info hash 不应再次连接 qB 写入端")

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: search_adapter,
            pt_factory=unexpected_pt,
            qb_factory=unexpected_qb,
        )

        assert (created, succeeded, failed) == (1, 0, 1)
        job = await session.scalar(select(AutomationJob).where(AutomationJob.media_id == media.id))
        assert job is not None
        assert job.download_id == existing_download.id
        assert job.state == AutomationJobState.FAILED
        assert expected_message in (job.error_message or "")


@pytest.mark.asyncio
async def test_duplicate_hash_for_another_media_is_an_explicit_conflict(
    session_factory,
) -> None:
    _, info_hash = torrent_with_size(20_000)
    async with session_factory() as session:
        existing_media = LibraryMediaItem(
            source_item_id="duplicate-other-media-existing",
            media_type=MediaType.MOVIE,
            tmdb_id=572,
            title="Existing Media",
            state=MediaState.DOWNLOADING,
        )
        target_media = LibraryMediaItem(
            source_item_id="duplicate-other-media-target",
            media_type=MediaType.MOVIE,
            tmdb_id=573,
            title="Target Media",
            state=MediaState.CANDIDATES,
        )
        session.add_all([existing_media, target_media])
        await session.flush()
        existing_search = ReleaseSearch(media_id=existing_media.id, site_ids=["avistaz"])
        target_search = ReleaseSearch(media_id=target_media.id, site_ids=["avistaz"])
        session.add_all([existing_search, target_search])
        await session.flush()
        existing_candidate = ReleaseCandidate(
            search_id=existing_search.id,
            site_id="avistaz",
            torrent_id="duplicate-other-media-existing",
            title="Existing.Media.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
            info_hash=info_hash,
        )
        target_candidate = ReleaseCandidate(
            search_id=target_search.id,
            site_id="avistaz",
            torrent_id="duplicate-other-media-target",
            title="Target.Media.1080p.WEB-DL",
            size_bytes=20_000,
            seeders=8,
            score=0.9,
            reasons=["TMDB_ID_EXACT"],
            warnings=[],
            info_hash=info_hash,
        )
        session.add_all([existing_candidate, target_candidate])
        await session.flush()
        existing_download = Download(
            media_id=existing_media.id,
            candidate_id=existing_candidate.id,
            info_hash=info_hash,
            name=existing_candidate.title,
            state=DownloadState.DOWNLOADING,
        )
        session.add(existing_download)
        await session.commit()

        def unexpected_pt(_site_id: str) -> PtSiteAdapter:
            raise AssertionError("跨影视重复 hash 不应再次获取种子")

        def unexpected_qb() -> QbittorrentAdapter:
            raise AssertionError("跨影视重复 hash 不应连接 qB 写入端")

        with pytest.raises(AppError) as caught:
            await submit_download(
                session,
                candidate_id=target_candidate.id,
                confirm_warnings=False,
                pt_factory=unexpected_pt,
                qb_factory=unexpected_qb,
            )

        assert caught.value.error_code == "DUPLICATE_DOWNLOAD_OTHER_MEDIA"
        await session.refresh(target_media)
        assert target_media.state == MediaState.NEEDS_ATTENTION
        assert target_media.attention_reason == (
            "相同种子已关联其他影视条目，当前数据模型不能跨影视复用下载"
        )
        downloads = list(await session.scalars(select(Download)))
        assert downloads == [existing_download]


@pytest.mark.asyncio
async def test_pending_job_from_an_interrupted_batch_is_processed(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-pending",
            media_type=MediaType.MOVIE,
            tmdb_id=540,
            title="Pending Movie",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        pending = AutomationJob(
            run_id="interrupted-batch",
            media_id=media.id,
            state=AutomationJobState.PENDING,
        )
        session.add(pending)
        await session.commit()
        await update_policy(session, AutomationPolicyUpdate(enabled=True, minimum_score=0.5))

        _, created, succeeded, failed = await run_dry_run(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(540, "pending-success")]
            ),
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert pending.state == AutomationJobState.SUCCEEDED
        jobs = list(await session.scalars(select(AutomationJob)))
        assert jobs == [pending]


@pytest.mark.asyncio
async def test_scheduler_syncs_downloads_even_when_search_policy_is_disabled(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    qb = cast(QbittorrentAdapter, object())

    async def fake_sync(_session: AsyncSession, _qb: QbittorrentAdapter) -> int:
        calls.append("sync")
        return 0

    async def fake_close(_adapter: object) -> None:
        calls.append("close")

    monkeypatch.setattr(automation_runner, "build_qb_readonly", lambda: qb)
    monkeypatch.setattr(automation_runner, "sync_download_statuses", fake_sync)
    monkeypatch.setattr(automation_runner, "close_adapter", fake_close)

    async with session_factory() as session:
        assert await automation_runner.run_scheduled_cycle(session) is False

    assert calls == ["sync", "close"]


@pytest.mark.asyncio
async def test_automation_identifies_a_unique_tmdb_match_before_search(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="automation-identify",
            media_type=MediaType.MOVIE,
            title="Unidentified Movie",
            year=2026,
            state=MediaState.NEEDS_ATTENTION,
        )
        session.add(media)
        await session.commit()
        await update_policy(session, AutomationPolicyUpdate(enabled=True, minimum_score=0.5))

        _, created, succeeded, failed = await run_automation(
            session,
            adapter_factory=lambda _site_id: AvistaZMockAdapter(
                fixtures=[movie_candidate(550, "identified-success")]
            ),
            metadata_factory=UniqueMetadataProvider,
        )

        assert (created, succeeded, failed) == (1, 1, 0)
        assert media.tmdb_id == 550
        assert media.title == "Identified Movie"
