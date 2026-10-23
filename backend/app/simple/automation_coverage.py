from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MediaType
from app.services.tv_pack import classify_tv_pack
from app.simple.models import (
    Download,
    DownloadCleanupState,
    DownloadState,
    Episode,
    EpisodeState,
    LibraryMediaItem,
    MediaSeason,
    ReleaseCandidate,
)

_EPISODE = re.compile(r"S(\d{1,2})E(\d{1,5})", re.IGNORECASE)
_FINISHED = {DownloadState.COMPLETED, DownloadState.SEEDING}
_UNCONFIRMED = {DownloadState.SUBMITTING, DownloadState.OUTCOME_UNKNOWN}
_TRANSFERRING = {DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED}
_REMOVED = {DownloadCleanupState.DELETED, DownloadCleanupState.VANISHED}


@dataclass(frozen=True)
class ExistingDownloadCoverage:
    """Current missing content and the union of usable downloads for one title.

    A result also exists for partial coverage: callers must use ``skip``, not
    merely the presence of this object, when excluding media from scheduling.
    Season coverage means a whole season, never just one episode of that season.
    """

    skip: bool
    reason: str | None
    download_ids: tuple[str, ...]
    waiting_for_library: bool = False
    awaiting_confirmation: bool = False
    covered_seasons: frozenset[int] = frozenset()
    covered_episode_codes: frozenset[str] = frozenset()
    missing_episode_codes: frozenset[str] = frozenset()
    uncovered_episode_codes: frozenset[str] = frozenset()
    missing_seasons: frozenset[int] = frozenset()
    uncovered_seasons: frozenset[int] = frozenset()


def _episode_codes(values: Sequence[str]) -> frozenset[str]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        match = _EPISODE.fullmatch(value.strip())
        if match and int(match[2]) > 0:
            result.add(f"S{int(match[1]):02d}E{int(match[2]):02d}")
    return frozenset(result)


def _episode_season(code: str) -> int:
    return int(code[1 : code.index("E")])


def _candidate_content(
    candidate: ReleaseCandidate | None,
    *,
    unfinished_seasons: frozenset[int] = frozenset(),
) -> tuple[frozenset[int], frozenset[str]]:
    if candidate is None:
        return frozenset(), frozenset()
    episodes = _episode_codes(candidate.episode_coverage or [])
    # Malformed episode coverage must not turn a partial release into a pack.
    if candidate.episode_coverage:
        return frozenset(), episodes
    pack = classify_tv_pack(
        candidate.title,
        collection_type=candidate.collection_type,
        seasons=candidate.season_coverage,
        episodes=candidate.episode_coverage,
        file_count=candidate.file_count,
    )
    if pack is None:
        return frozenset(), episodes
    # An unnumbered "complete series" is not evidence about a newly added
    # season. Nor can an old pack cover future episodes of an airing season.
    seasons = frozenset(
        number
        for number in candidate.season_coverage or []
        if isinstance(number, int)
        and not isinstance(number, bool)
        and number > 0
        and number not in unfinished_seasons
    )
    return seasons, episodes


def evaluate_download_coverage(
    media: LibraryMediaItem,
    downloads: Sequence[tuple[Download, ReleaseCandidate | None]],
    *,
    episodes: Sequence[Episode] = (),
    seasons: Sequence[MediaSeason] = (),
) -> ExistingDownloadCoverage | None:
    usable = [
        (download, candidate)
        for download, candidate in downloads
        if download.media_id == media.id
        and download.state != DownloadState.ERROR
        and download.cleanup_state not in _REMOVED
    ]
    if not usable:
        return None

    media_episodes = [episode for episode in episodes if episode.media_id == media.id]
    media_seasons = [season for season in seasons if season.media_id == media.id]
    missing = frozenset(
        f"S{episode.season_number:02d}E{episode.episode_number:02d}"
        for episode in media_episodes
        if episode.state in {EpisodeState.MISSING, EpisodeState.DOWNLOADING}
    )
    recorded_seasons = {episode.season_number for episode in media_episodes}
    # When NextFind supplies no per-episode view for a season, keep that season
    # as a requirement. Otherwise an old S01 would suppress a newly known S02.
    missing_seasons = frozenset(
        season.season_number
        for season in media_seasons
        if season.season_number > 0
        and season.season_number not in recorded_seasons
        and (season.aired_episode_count > 0 or season.is_complete)
    )
    unfinished = frozenset(
        season.season_number for season in media_seasons if not season.is_complete
    )
    covered_seasons: set[int] = set()
    covered_episodes: set[str] = set()
    finished_seasons: set[int] = set()
    finished_episodes: set[str] = set()
    for download, candidate in usable:
        candidate_seasons, candidate_episodes = _candidate_content(
            candidate, unfinished_seasons=unfinished
        )
        covered_seasons.update(candidate_seasons)
        covered_episodes.update(candidate_episodes)
        if download.state in _FINISHED:
            finished_seasons.update(candidate_seasons)
            finished_episodes.update(candidate_episodes)

    uncovered = frozenset(
        code
        for code in missing
        if code not in covered_episodes and _episode_season(code) not in covered_seasons
    )
    seasons_by_number = {season.season_number: season for season in media_seasons}

    def remaining_seasons(whole_seasons: set[int], episode_codes: set[str]) -> frozenset[int]:
        remaining: set[int] = set()
        for number in missing_seasons.difference(whole_seasons):
            season = seasons_by_number[number]
            count = season.episode_count if season.is_complete else season.aired_episode_count
            # Separate single-episode downloads may collectively cover the
            # known aired season even when NextFind has no episode rows yet.
            if not count or any(
                f"S{number:02d}E{episode:02d}" not in episode_codes
                for episode in range(1, count + 1)
            ):
                remaining.add(number)
        return frozenset(remaining)

    uncovered_seasons = remaining_seasons(covered_seasons, covered_episodes)
    finished_cover_missing = not remaining_seasons(finished_seasons, finished_episodes) and all(
        code in finished_episodes or _episode_season(code) in finished_seasons for code in missing
    )
    unconfirmed = any(download.state in _UNCONFIRMED for download, _ in usable)
    transferring = any(download.state in _TRANSFERRING for download, _ in usable)
    finished = any(download.state in _FINISHED for download, _ in usable)
    has_requirements = bool(missing or missing_seasons)
    is_movie = media.media_type == MediaType.MOVIE
    # With no per-season or per-episode evidence, repeat searches cannot prove
    # that another copy is needed. Await import/reconciliation; explicit new
    # missing content on a later sync immediately re-enables coverage checks.
    # Submission permits only one unfinished transfer per title. Keep partial
    # coverage so new seasons become eligible as soon as that transfer finishes.
    skip = (
        unconfirmed
        or transferring
        or is_movie
        or not has_requirements
        or not (uncovered or uncovered_seasons)
    )
    waiting = (
        skip
        and not unconfirmed
        and not transferring
        and finished
        and (is_movie or not has_requirements or finished_cover_missing)
        and media.library_confirmed_at is None
    )
    reason = None
    if unconfirmed:
        reason = "已有下载提交结果待确认，等待下载器同步"
    elif transferring:
        reason = "已有下载任务进行中，等待当前任务完成"
    elif waiting:
        reason = "已有下载已完成，等待 NextFind 确认入库"
    elif skip:
        reason = "已有有效下载覆盖当前缺失内容"
    return ExistingDownloadCoverage(
        skip=skip,
        reason=reason,
        download_ids=tuple(download.id for download, _ in usable),
        waiting_for_library=waiting,
        awaiting_confirmation=unconfirmed,
        covered_seasons=frozenset(covered_seasons),
        covered_episode_codes=frozenset(covered_episodes),
        missing_episode_codes=missing,
        uncovered_episode_codes=uncovered,
        missing_seasons=missing_seasons,
        uncovered_seasons=uncovered_seasons,
    )


def candidate_adds_missing_content(
    candidate: ReleaseCandidate, coverage: ExistingDownloadCoverage
) -> bool:
    """Do not choose S01 again while the uncovered requirement is S02.

    Unknown candidate coverage is left to identity deduplication and the normal
    candidate checks. It must not be guessed to cover an entire series.
    """

    candidate_seasons, candidate_episodes = _candidate_content(candidate)
    if not candidate_seasons and not candidate_episodes:
        return True
    if candidate_episodes:
        new_episodes = {
            code
            for code in candidate_episodes
            if code not in coverage.covered_episode_codes
            and _episode_season(code) not in coverage.covered_seasons
        }
        if coverage.missing_episode_codes or coverage.missing_seasons:
            return any(
                code in coverage.uncovered_episode_codes
                or _episode_season(code) in coverage.uncovered_seasons
                for code in new_episodes
            )
        return bool(new_episodes)
    new_seasons = candidate_seasons.difference(coverage.covered_seasons)
    if coverage.missing_episode_codes or coverage.missing_seasons:
        return bool(new_seasons.intersection(coverage.uncovered_seasons)) or any(
            _episode_season(code) in new_seasons for code in coverage.uncovered_episode_codes
        )
    return bool(new_seasons)


async def existing_download_coverage(
    session: AsyncSession, media_items: Sequence[LibraryMediaItem]
) -> dict[str, ExistingDownloadCoverage]:
    """Load coverage in bounded batches without a query for every media item."""

    result: dict[str, ExistingDownloadCoverage] = {}
    for offset in range(0, len(media_items), 500):
        batch = media_items[offset : offset + 500]
        ids = [media.id for media in batch]
        rows = await session.execute(
            select(Download, ReleaseCandidate)
            .outerjoin(ReleaseCandidate, ReleaseCandidate.id == Download.candidate_id)
            .where(
                Download.media_id.in_(ids),
                Download.state != DownloadState.ERROR,
                Download.cleanup_state.not_in(_REMOVED),
            )
            .order_by(Download.created_at, Download.id)
        )
        by_media: dict[str, list[tuple[Download, ReleaseCandidate | None]]] = defaultdict(list)
        for download, candidate in rows:
            by_media[download.media_id].append((download, candidate))
        tv_ids = [
            media.id for media in batch if media.media_type == MediaType.TV and media.id in by_media
        ]
        episode_map: dict[str, list[Episode]] = defaultdict(list)
        season_map: dict[str, list[MediaSeason]] = defaultdict(list)
        if tv_ids:
            for episode in await session.scalars(
                select(Episode).where(Episode.media_id.in_(tv_ids))
            ):
                episode_map[episode.media_id].append(episode)
            for season in await session.scalars(
                select(MediaSeason).where(MediaSeason.media_id.in_(tv_ids))
            ):
                season_map[season.media_id].append(season)
        for media in batch:
            coverage = evaluate_download_coverage(
                media,
                by_media[media.id],
                episodes=episode_map[media.id],
                seasons=season_map[media.id],
            )
            if coverage is not None:
                result[media.id] = coverage
    return result
